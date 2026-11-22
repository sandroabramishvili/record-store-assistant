"""Test doubles for the graph: no Gemini, no database (spec 12: no real LLM calls in tests).

FakeLLM  — `with_structured_output(Schema)` returns the next queued object of that type
           and records every prompt it received (so tests can check what the LLM saw).
FakeRepo — an in-memory StoreRepository with the same method names.
"""

import copy
import inspect
from collections import Counter
from decimal import Decimal

from langchain_core.runnables import RunnableLambda

from app.services.customer import CustomerContext
from app.services.reservation_rules import MAX_ACTIVE, ReservationConflict, ReservationLimitReached


class Reply:
    """A response computed from the prompt, e.g. "feature the ids you were given"."""

    def __init__(self, schema, make):
        self.schema, self.make = schema, make


def feature_available(message='Here you go.', count=3):
    """AnswerDraft that features the first `count` ids from <available_copies> (no prices)."""
    import json
    from app.ai.schemas import AnswerDraft

    def make(messages):
        human = messages[-1][1]
        block = human.split('<available_copies>\n')[1].split('\n</available_copies>')[0]
        return AnswerDraft(message=message, featured_item_ids=[c['id'] for c in json.loads(block)][:count])
    return Reply(AnswerDraft, make)


class FakeLLM:
    def __init__(self, *responses):
        self.queue = list(responses)      # RequestAnalysis / AnswerDraft objects, Reply, or Exceptions to raise
        self.calls: list[tuple[str, list]] = []

    def add(self, *responses):
        self.queue.extend(responses)

    def with_structured_output(self, schema):
        async def respond(messages):
            self.calls.append((schema.__name__, messages))
            for i, item in enumerate(self.queue):
                if isinstance(item, Reply) and item.schema is schema:
                    self.queue.pop(i)
                    result = item.make(messages)
                    return await result if inspect.isawaitable(result) else result   # async make: e.g. a hang
                if isinstance(item, schema) or (isinstance(item, Exception) and getattr(item, 'schema', schema) is schema):
                    self.queue.pop(i)
                    if isinstance(item, Exception):
                        raise item
                    return item
            raise AssertionError(f'FakeLLM: no {schema.__name__} queued')
        return RunnableLambda(respond)

    def prompts(self, schema_name: str) -> list[str]:
        """The human-message text of every call for one schema."""
        return [msgs[-1][1] for name, msgs in self.calls if name == schema_name]


def copy_row(item_id, record_id, artist, title, *, price='30.00', grade='VG+', status='in_stock', year=1973,
             release_year=None, genres=('jazz',), styles=()):
    from app.services.normalize import normalize_name
    return {'item_id': item_id, 'record_id': record_id, 'artist': artist, 'title': title,
            'artist_norm': normalize_name(artist), 'title_norm': normalize_name(title),
            'original_year': year, 'release_year': release_year or year, 'is_reissue': (release_year or year) != year,
            'format': 'LP', 'genres': list(genres), 'styles': list(styles), 'status': status,
            'price': price, 'media_condition': grade, 'sleeve_condition': 'VG'}


class FakeRepo:
    def __init__(self, items=(), ctx: CustomerContext | None = None):
        self.items = {i['item_id']: copy.deepcopy(i) for i in items}
        self.ctx = ctx or CustomerContext()
        self.active = 0
        self.reserved: list[int] = []
        self.wantlist: list[dict] = []
        self.logs: list[tuple] = []
        self.chunks: list[dict] = []
        self.details: list[dict] = []
        self.stats = {'total': 0, 'by_genre': {}, 'by_decade': {}, 'records': []}
        self.searches: list[dict] = []
        self.calls: Counter[str] = Counter()     # how often each method ran (interrupt re-run tests)

    async def load_context(self, user_id):
        return self.ctx

    async def search(self, constraints, ctx, *, limit=20):
        """Tiny stand-in for hybrid search: in stock, artist and price filters only."""
        self.searches.append(copy.deepcopy(constraints))
        out = [i for i in self.items.values() if i['status'] == 'in_stock']
        if constraints.get('artists'):
            out = [i for i in out if i['artist'].lower() in [a.lower() for a in constraints['artists']]]
        if constraints.get('max_price'):
            out = [i for i in out if Decimal(i['price']) <= Decimal(str(constraints['max_price']))]
        return [copy.deepcopy(i) | {'score': 0.5} for i in out][:limit]

    async def get_items(self, ids):
        return {i: copy.deepcopy(self.items[i]) for i in ids if i in self.items}

    async def log_search(self, user_id, intent, constraints, results, relax_attempts):
        self.logs.append((intent, results, relax_attempts))

    async def active_reservation_count(self, user_id):
        self.calls['active_reservation_count'] += 1
        return self.active

    async def reserve(self, user_id, item_id):
        self.calls['reserve'] += 1
        if self.active >= MAX_ACTIVE:
            raise ReservationLimitReached()
        if self.items[item_id]['status'] != 'in_stock':
            raise ReservationConflict()
        self.items[item_id]['status'] = 'reserved'
        self.active += 1
        self.reserved.append(item_id)
        return {'reservation_id': len(self.reserved), 'inventory_item_id': item_id, 'expires_at': '2026-11-25T18:00:00+00:00'}

    async def add_wantlist(self, user_id, artist, title=None, max_price=None, min_condition=None):
        entry = {'wantlist_id': len(self.wantlist) + 1, 'artist': artist, 'title': title, 'max_price': max_price,
                 'min_condition': min_condition, 'matched_item_id': None, 'created': True}
        self.wantlist.append(entry)
        return entry

    async def search_knowledge(self, query, *, k=None, min_score=None):
        return self.chunks

    async def record_details(self, *, titles=(), artists=(), limit=6):
        return self.details if (titles or artists) else []

    async def catalog_tags(self):
        return sorted({t for i in self.items.values() for t in [*i['genres'], *i['styles']]})

    async def catalog_titles(self):
        from app.services.normalize import normalize_name
        titles: dict[str, list[int]] = {}
        for i in self.items.values():
            titles.setdefault(normalize_name(i['title']), []).append(i['record_id'])
        return titles

    async def collection_stats(self, user_id):
        return self.stats
