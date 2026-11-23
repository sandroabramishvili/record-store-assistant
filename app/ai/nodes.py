"""The graph's nodes, as methods of one class that holds `Deps`.

Each node reads the state and returns ONLY the fields it changes. Nodes never open DB
sessions or create models: everything external goes through `self.deps`.
This module imports nothing that loads the configuration or the database, so the nodes
can be tested with a fake LLM and a fake repository.

"The LLM talks, the code decides": the LLM is called in exactly two places
(understand_request, compose_answer). Stock, prices, ownership, which copy "the second
one" is, and whether an action succeeded are all decided here, in code.
"""

import copy
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage
from langgraph.types import interrupt

from app.ai.llm import INVALID_OUTPUT_ERRORS, analyzer, composer
from app.ai.prompts import CLARIFY_MESSAGE, REFUSAL_MESSAGE, compose_messages, knowledge_messages, understand_messages
from app.ai.schemas import AnswerDraft, RequestAnalysis
from app.ai.state import StoreState
from app.services.availability import filter_available
from app.services.customer import CustomerContext
from app.ai.routing import MAX_ANSWER_ATTEMPTS, RELAX_EXHAUSTED
from app.services.relax import MAX_RELAX_ATTEMPTS, relax_constraints
from app.services.reservation_rules import HOLD_HOURS, MAX_ACTIVE, ReservationError
from app.services.verify import verify_draft

if TYPE_CHECKING:
    from app.ai.deps import Deps

logger = logging.getLogger(__name__)

MAX_AVAILABLE = 8              # copies handed to the LLM (it features at most 5)
MAX_STORED_MESSAGES = 20       # messages kept in the checkpoint (the prompts use far fewer)
UNCLEAR = 'unclear'            # set by code (never by the LLM) when the request couldn't be parsed
MAX_CARDS = 5
# RELAX_EXHAUSTED (from routing): marker that nothing is left to loosen → stop searching


# ---------- small pure helpers ----------


def split_conversation(messages: list[AnyMessage]) -> tuple[str, list[tuple[str, str]]]:
    """(latest customer message, earlier turns as (role, text))."""
    *earlier, last = messages
    role = {'human': 'customer', 'ai': 'assistant'}
    history = [(role.get(m.type, m.type), m.content if isinstance(m.content, str) else str(m.content))
               for m in earlier if m.type in role]
    return str(last.content), history


def card_label(item: dict) -> str:
    """"Herbie Hancock — Head Hunters (1973), VG+/VG, $38.00" — the numbered-list line."""
    pressing = f', {item["release_year"]} reissue' if item.get('is_reissue') else ''
    return (f'{item["artist"]} — {item["title"]} ({item["original_year"]}{pressing}), '
            f'{item["media_condition"]}/{item["sleeve_condition"]}, ${item["price"]}')


def for_llm(item: dict) -> dict:
    """Only what the LLM needs to describe a copy (small prompt, no internal fields)."""
    return {
        'id': item['item_id'], 'artist': item['artist'], 'title': item['title'],
        'year': item['original_year'], 'pressing': f'{item["release_year"]} reissue' if item['is_reissue'] else 'original',
        'format': item['format'], 'genres': item['genres'] + item['styles'],
        'media_condition': item['media_condition'], 'sleeve_condition': item['sleeve_condition'], 'price': item['price'],
    }


def relax_notes(asked: dict, used: dict) -> list[str]:
    """What the relax loop changed, so the answer can be honest about it."""
    notes = []
    if asked.get('max_price') and used.get('max_price') != asked.get('max_price'):
        notes.append(f'Nothing matched under ${asked["max_price"]:g}, so the price limit was raised to ${used["max_price"]:g}.')
    if asked.get('decade') and not used.get('decade'):
        notes.append(f'Nothing matched from the {asked["decade"]}s, so other decades were included.')
    if asked.get('genres') and not used.get('genres'):
        notes.append(f'Nothing in stock is tagged {" / ".join(asked["genres"])}, so these are the closest '
                     'records by sound from other styles. Say so; do not present them as that genre.')
    return notes


def money_or_none(value: Any) -> Decimal | None:
    return None if value in (None, '') else Decimal(str(value))


# ---------- nodes ----------


def keep_recent(state: StoreState, new_message: AIMessage) -> list[AnyMessage]:
    """The `messages` update that ends a turn: the answer, plus RemoveMessage for the oldest
    messages beyond MAX_STORED_MESSAGES. The prompts only ever use the last few turns
    (prompts.HISTORY_TURNS), so older ones only made every checkpoint bigger."""
    stored = state.get('messages') or []
    overflow = len(stored) + 1 - MAX_STORED_MESSAGES
    return [RemoveMessage(id=m.id) for m in stored[:max(overflow, 0)] if m.id] + [new_message]


class Nodes:
    def __init__(self, deps: 'Deps'):
        self.deps = deps
        self.analyzer = analyzer(deps.llm)
        self.composer = composer(deps.llm)

    # ---- understanding ----

    async def understand_request(self, state: StoreState) -> dict:
        """LLM #1: intent + constraints + "which earlier record" (as a position, never an id)."""
        message, history = split_conversation(state['messages'])
        shown = state.get('shown_item_ids') or []
        items = await self.deps.repo.get_items(shown) if shown else {}
        last_shown = [
            card_label(items[i]) + ('' if items[i]['status'] == 'in_stock' else ' (no longer available)')
            if i in items else '(no longer in the catalog)'
            for i in shown
        ]
        try:
            shop_tags = await self.deps.repo.catalog_tags()
            analysis: RequestAnalysis = await self.analyzer.ainvoke(
                understand_messages(message, history, last_shown, shop_tags))
        except INVALID_OUTPUT_ERRORS:
            # Three invalid answers in a row: ask the customer to rephrase (polite_refusal
            # handles UNCLEAR with a fixed text). Network errors are not caught: they reach
            # the API, which keeps the message and says "try again".
            logger.warning('request could not be understood', exc_info=True)
            return {'intent': UNCLEAR}
        constraints = analysis.constraints.model_dump()
        intent, reserve_requested = analysis.intent, False
        if (intent == 'reserve' and analysis.reference_index is None
                and (constraints.get('titles') or constraints.get('artists'))):
            # "Do you have Abbey Road? If so, reserve it." names a record but points at no shown
            # copy. Found in a real run: prepare_reservation (correctly) reserved nothing, and the
            # answer then claimed "not in stock" without any search. Code decides: search first,
            # show the copies, and the customer picks one ("the first one") → confirmation.
            intent, reserve_requested = 'find_record', True
        return {
            'intent': intent,
            'reserve_requested': reserve_requested,
            'constraints': constraints,
            'request_constraints': copy.deepcopy(constraints),   # the relax loop must not change this one
            'refers_to_previous': analysis.refers_to_previous,
            'reference_index': analysis.reference_index,
        }

    async def polite_refusal(self, state: StoreState) -> dict:
        """off_topic, lyrics, downloads: a fixed text, no LLM call (instant, free, predictable)."""
        text = CLARIFY_MESSAGE if state.get('intent') == UNCLEAR else REFUSAL_MESSAGE
        return {'answer': text, 'messages': keep_recent(state, AIMessage(content=text))}

    async def load_context(self, state: StoreState) -> dict:
        """Profile + collection + wantlist artists. user_id comes from the JWT, never the LLM."""
        ctx = await self.deps.repo.load_context(state['user_id'])
        return {'profile': ctx.profile, 'collection': ctx.collection, 'wantlist_artists': ctx.wantlist_artists}

    # ---- finding records ----

    async def search_inventory(self, state: StoreState) -> dict:
        found = await self.deps.repo.search(state['constraints'], CustomerContext.from_state(state))
        return {'candidates': found}

    async def availability_filter(self, state: StoreState) -> dict:
        """The store rules (services/availability.py), the last gate: only these copies may reach the LLM."""
        ctx = CustomerContext.from_state(state)
        available = filter_available(
            state.get('candidates') or [],
            profile=ctx.profile, owned_record_ids=ctx.owned_record_ids, owned_keys=ctx.owned_keys,
            requested_genres=state['request_constraints'].get('genres') or [],
            include_owned=bool(state['constraints'].get('include_owned')),
        )[:MAX_AVAILABLE]
        if available or state.get('relax_attempts', 0) >= MAX_RELAX_ATTEMPTS:
            await self._log_search(state, len(available))          # final outcome of this search
        return {'available_items': available}

    async def relax_constraints(self, state: StoreState) -> dict:
        """Loosen the soft constraints (price +25 %, then decade/genres). Never the hard rules."""
        attempt = state.get('relax_attempts', 0) + 1
        relaxed = relax_constraints(state['constraints'], attempt, state.get('profile') or {})
        if relaxed == state['constraints']:                         # nothing left to loosen
            await self._log_search(state, 0, relax_attempts=attempt - 1)
            return {'relax_attempts': RELAX_EXHAUSTED}
        return {'constraints': relaxed, 'relax_attempts': attempt}

    async def offer_wantlist(self, state: StoreState) -> dict:
        """Still nothing: prepare "add to your wantlist?" (needs at least an artist)."""
        return {'pending_action': await self._wantlist_action(state, 'Nothing matches right now. ')}

    # ---- information ----

    async def retrieve_knowledge(self, state: StoreState) -> dict:
        """KB chunks above the score threshold + catalog facts for records the customer named."""
        message, _ = split_conversation(state['messages'])
        asked = state['request_constraints']
        chunks = await self.deps.repo.search_knowledge(message)
        details = await self.deps.repo.record_details(titles=asked.get('titles') or [], artists=asked.get('artists') or [])
        return {'knowledge_chunks': chunks, 'catalog_details': details}

    async def collection_insights(self, state: StoreState) -> dict:
        """Stats (SQL) + "which albums by X am I missing that you have?" (search minus owned)."""
        stats = await self.deps.repo.collection_stats(state['user_id'])
        asked = state['request_constraints']
        missing: list[dict] = []
        if asked.get('artists'):
            ctx = CustomerContext.from_state(state)
            found = await self.deps.repo.search({'query_text': asked.get('query_text') or '', 'artists': asked['artists']},
                                                ctx, limit=50)
            seen = set()
            for item in filter_available(found, profile=ctx.profile, owned_record_ids=ctx.owned_record_ids,
                                         owned_keys=ctx.owned_keys):
                if item['record_id'] not in seen:                  # one copy per album (best ranked)
                    seen.add(item['record_id'])
                    missing.append(item)
        return {'catalog_details': [{'collection_stats': stats}], 'available_items': missing[:MAX_AVAILABLE]}

    # ---- actions (Human-in-the-Loop) ----

    async def prepare_reservation(self, state: StoreState) -> dict:
        """Resolve WHICH copy by code: reference_index → shown_item_ids. Re-check it is still free."""
        shown = state.get('shown_item_ids') or []
        index = state.get('reference_index')
        if index is None and len(shown) == 1:
            index = 1                                              # only one was shown: "that one"
        if not shown or index is None or not 1 <= index <= len(shown):
            return {'pending_action': None,
                    'action_result': {'type': 'reserve', 'status': 'unclear', 'shown_count': len(shown)}}

        item_id = shown[index - 1]
        item = (await self.deps.repo.get_items([item_id])).get(item_id)
        if item is None or item['status'] != 'in_stock':
            label = card_label(item) if item else 'that copy'
            return {'pending_action': None, 'action_result': {'type': 'reserve', 'status': 'unavailable', 'label': label}}
        if await self.deps.repo.active_reservation_count(state['user_id']) >= MAX_ACTIVE:
            return {'pending_action': None, 'action_result': {'type': 'reserve', 'status': 'limit'}}

        label = card_label(item)
        return {'pending_action': {
            'type': 'reserve', 'inventory_item_id': item_id, 'label': label, 'price': item['price'],
            'hold_hours': HOLD_HOURS, 'question': f'Reserve {label} for {HOLD_HOURS} hours?',
        }}

    async def prepare_wantlist_entry(self, state: StoreState) -> dict:
        action = await self._wantlist_action(state, '')
        if action is None:
            return {'pending_action': None, 'action_result': {'type': 'wantlist', 'status': 'need_artist'}}
        return {'pending_action': action}

    async def confirm_action(self, state: StoreState) -> dict:
        """Pause the graph and ask the customer. Resumes with Command(resume=...) from the /confirm endpoint."""
        action = state['pending_action']
        # The SSE event shape: {"type": "confirmation_required", "action": "reserve", ...details}
        answer = interrupt({**action, 'type': 'confirmation_required', 'action': action['type']})
        confirmed = answer.get('confirmed') if isinstance(answer, dict) else answer
        return {'confirmed': confirmed is True}

    async def execute_action(self, state: StoreState) -> dict:
        """Only after an explicit "yes". Uses the same atomic services as the API."""
        action = state['pending_action']
        if not state.get('confirmed'):
            return {'pending_action': None, 'action_result': {'type': action['type'], 'status': 'cancelled'}}
        if action['type'] == 'reserve':
            try:
                done = await self.deps.repo.reserve(state['user_id'], action['inventory_item_id'])
                result = {'type': 'reserve', 'status': 'reserved', 'label': action['label'], 'price': action['price'], **done}
            except ReservationError as error:                      # taken meanwhile / limit: a sentence, not a crash
                result = {'type': 'reserve', 'status': 'failed', 'label': action['label'], 'reason': str(error)}
        else:
            entry = await self.deps.repo.add_wantlist(state['user_id'], action['artist'], action.get('title'),
                                                      action.get('max_price'), action.get('min_condition'))
            result = {'type': 'wantlist', 'status': 'added', **entry}
        return {'pending_action': None, 'action_result': result}

    # ---- answer ----

    async def compose_answer(self, state: StoreState) -> dict:
        """LLM #2: writes the words. It only sees the data in the state."""
        message, history = split_conversation(state['messages'])
        issues = state.get('answer_issues') or []
        if state.get('intent') == 'record_info':
            prompt = knowledge_messages(message, chunks=state.get('knowledge_chunks') or [],
                                        catalog_details=state.get('catalog_details') or [], history=history, issues=issues)
        else:
            prompt = compose_messages(message, available=[for_llm(i) for i in state.get('available_items') or []],
                                      history=history, notes=self._notes(state), issues=issues)
        try:
            draft: AnswerDraft = await self.composer.ainvoke(prompt)
        except INVALID_OUTPUT_ERRORS:
            # No usable draft after 3 attempts: no point in a 2nd compose round, go straight
            # to fallback_template (a correct answer built from the state by code).
            logger.warning('no valid answer draft', exc_info=True)
            return {'draft': None, 'answer_attempts': MAX_ANSWER_ATTEMPTS}
        return {'draft': draft.model_dump(), 'answer_attempts': state.get('answer_attempts', 0) + 1}

    async def verify_answer(self, state: StoreState) -> dict:
        """Code checks the draft (ids, prices, stock claims). Pass → final answer; fail → issues."""
        draft = state['draft']
        if draft is None:                                        # compose_answer gave up (invalid output)
            return {'answer_issues': ['the model returned no valid answer']}
        text = draft['message'] + (f'\n\n{draft["follow_up_question"]}' if draft.get('follow_up_question') else '')
        allowed = self._verifiable_items(state)
        titles = {title: set(ids) for title, ids in (await self.deps.repo.catalog_titles()).items()}
        issues = verify_draft(featured_ids=draft['featured_item_ids'], message=text,
                              available_by_id=allowed, extra_amounts=self._extra_amounts(state), catalog_titles=titles)
        # A recommendation with matching copies must show at least one (found in the first real run:
        # "you might enjoy ... like these!" with no cards).
        if (state.get('intent') in ('find_record', 'collection_help') and state.get('available_items')
                and not state.get('action_result') and not draft['featured_item_ids']):
            issues.append('copies matching the request are available, but none was featured: '
                          'feature the best 1-5 of them by id')
        if issues:
            logger.info('answer rejected (attempt %s): %s', state.get('answer_attempts'), issues)
            return {'answer_issues': issues}
        return self._finish(state, text, draft['featured_item_ids'][:MAX_CARDS])

    async def fallback_template(self, state: StoreState) -> dict:
        """Two rejected drafts: build a plain, correct answer from the state, without the LLM."""
        if state.get('intent') == 'record_info':
            text = ("I'm sorry, I couldn't put together a reliable answer to that. "
                    'A staff member will be happy to help you in the shop.')
            return self._finish(state, text, [])
        parts = [self._action_sentence(state['action_result'])] if state.get('action_result') else []
        available = (state.get('available_items') or [])[:MAX_CARDS]
        if available:
            parts.append('Here is what we have in stock that matches your request:')
        elif state.get('intent') in ('find_record', 'collection_help') and not state.get('action_result'):
            parts.append("Sorry, nothing in stock matches that right now. Try a wider price range or condition.")
        return self._finish(state, '\n'.join(parts), [i['item_id'] for i in available])

    # ---------- internals ----------

    async def _wantlist_action(self, state: StoreState, prefix: str) -> dict | None:
        asked = state['request_constraints']
        artist = (asked.get('artists') or [None])[0]
        title = (asked.get('titles') or [None])[0]
        if not artist and title:
            # "Do you have Nevermind?" names no artist; if the title is in our catalog, code
            # knows it (found in a real run: the LLM sometimes leaves artists empty).
            known = await self.deps.repo.record_details(titles=[title], limit=1)
            if known:
                artist, title = known[0]['artist'], known[0]['title']
        if not artist:
            return None
        what = f'{artist} – {title}' if title else f'anything by {artist}'
        return {'type': 'wantlist', 'artist': artist, 'title': title, 'max_price': asked.get('max_price'),
                'min_condition': asked.get('min_condition'),
                'question': f"{prefix}Shall I add {what} to your wantlist and let you know when a copy comes in?"}

    def _notes(self, state: StoreState) -> list[str]:
        """Facts from code that the answer should mention."""
        notes = relax_notes(state.get('request_constraints') or {}, state.get('constraints') or {})
        if state.get('reserve_requested') and state.get('available_items'):
            notes.append('The customer wants to reserve this. Nothing is reserved yet: show the copies and ask '
                         'which one to hold (e.g. "the first one"); holding it needs their confirmation.')
        if state.get('action_result'):
            notes.append(self._action_sentence(state['action_result']))
        for detail in state.get('catalog_details') or []:
            if 'collection_stats' in detail:
                notes.append(f'Customer collection summary: {detail["collection_stats"]}')
        if state.get('intent') == 'find_record' and not state.get('available_items') and not state.get('action_result'):
            notes.append('Nothing in stock matches the request, even after loosening it. Suggest widening it.')
        return notes

    @staticmethod
    def _action_sentence(result: dict) -> str:
        status = result['status']
        if result['type'] == 'reserve':
            return {
                'reserved': f'Reservation confirmed: {result.get("label")} is held for the customer for {HOLD_HOURS} hours '
                            f'(until {result.get("expires_at", "")[:16].replace("T", " ")} UTC).',
                'failed': f'The reservation of {result.get("label")} failed: {result.get("reason")}',
                'cancelled': 'The customer cancelled; nothing was reserved.',
                'unavailable': f'{result.get("label")} is no longer available (reserved or sold meanwhile).',
                'limit': f'The customer already has {MAX_ACTIVE} active reservations, the maximum.',
                'unclear': 'It is unclear which record the customer means'
                           + (f'; ask them to pick a number from 1 to {result["shown_count"]}.' if result.get('shown_count')
                              else '; no records have been shown yet, so ask what they are looking for.'),
            }[status]
        return {
            'added': f'Added to the wantlist: {result.get("artist")}' + (f' – {result["title"]}' if result.get('title') else '')
                     + ('. A matching copy is already in stock!' if result.get('matched_item_id')
                        else '. The customer will see a match as soon as a copy comes in.'),
            'cancelled': 'The customer decided not to add it to the wantlist.',
            'need_artist': 'To add something to the wantlist, the artist is needed: ask which artist (and album).',
        }[status]

    @staticmethod
    def _verifiable_items(state: StoreState) -> dict[int, dict]:
        """Copies the answer may feature or price: available items, or catalog copies for record_info."""
        allowed = {i['item_id']: {'price': i['price'], 'record_id': i['record_id']}
                   for i in state.get('available_items') or []}
        for detail in state.get('catalog_details') or []:
            for c in detail.get('copies_in_stock', []):
                allowed[c['id']] = {'price': c['price'], 'record_id': detail['record_id']}
        return allowed

    @staticmethod
    def _extra_amounts(state: StoreState) -> list[Decimal]:
        """Other numbers the answer may quote: limits the customer set, the budget, an action's price."""
        asked, used = state.get('request_constraints') or {}, state.get('constraints') or {}
        values = [asked.get('max_price'), used.get('max_price'), (state.get('profile') or {}).get('budget_cap'),
                  (state.get('action_result') or {}).get('price')]
        return [m for m in map(money_or_none, values) if m is not None]

    def _finish(self, state: StoreState, text: str, featured: list[int]) -> dict:
        """The final answer. The history message gets a numbered list, so "the second one" works next turn."""
        labels = {i['item_id']: card_label(i) for i in state.get('available_items') or []}
        for d in state.get('catalog_details') or []:
            for c in d.get('copies_in_stock', []):
                labels[c['id']] = (f'{d["artist"]} — {d["title"]} ({d["release_year"]} {d["pressing"]}), '
                                   f'{c["media_condition"]}/{c["sleeve_condition"]}, ${c["price"]}')
        listing = '\n'.join(f'{n}. {labels[i]}' for n, i in enumerate(featured, start=1) if i in labels)
        return {
            'answer': text,
            'shown_item_ids': featured or state.get('shown_item_ids') or [],   # keep the old list if nothing new
            'messages': keep_recent(state, AIMessage(content=f'{text}\n\n{listing}' if listing else text)),
            'answer_issues': [],
        }

    async def _log_search(self, state: StoreState, results: int, relax_attempts: int | None = None) -> None:
        """SearchLog row. Never lets a logging problem break the chat."""
        if state.get('intent') != 'find_record':
            return
        attempts = min(state.get('relax_attempts', 0) if relax_attempts is None else relax_attempts, MAX_RELAX_ATTEMPTS)
        try:
            await self.deps.repo.log_search(state['user_id'], state['intent'], state['request_constraints'],
                                            results, attempts)
        except Exception:
            logger.exception('could not write SearchLog')
