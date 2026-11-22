"""The graph's state (spec 4.1): one dict that every node reads and partially updates.

LangGraph saves this state in a checkpoint after every node (Phase 11: in Postgres),
per chat (`thread_id`). The next message in the same chat starts from the saved state.

Two kinds of fields:
- PER-TURN fields belong to one customer message. They MUST be reset at the start of
  every turn, otherwise e.g. `relax_attempts` from the last message would carry over
  and the new search would stop relaxing early.  → `new_turn_input()`
- PERSISTENT fields live across turns: the conversation itself, and `shown_item_ids`,
  so "the second one" in the next message can be resolved.

Keep it small and JSON-safe (ids, titles, prices as strings), never whole DB rows:
a big state makes every checkpoint big.
"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages


class StoreState(TypedDict, total=False):
    # ---- persistent across turns ----
    messages: Annotated[list[AnyMessage], add_messages]   # reducer: new messages are APPENDED
    user_id: int                     # from the JWT (set by the server, never by the LLM)
    shown_item_ids: list[int]        # copies in the last answer, in order ("the second one" → [1])

    # ---- result of understand_request ----
    intent: str                      # find_record | record_info | reserve | wantlist | collection_help | off_topic
    request_constraints: dict        # + as the customer asked (never relaxed): for the answer and SearchLog
    constraints: dict                # what the search currently uses (relax_constraints loosens it)
    refers_to_previous: bool
    reference_index: int | None      # "the second one" → 2
    reserve_requested: bool          # + "reserve X" with nothing shown → searched first (Phase 14)

    # ---- loaded from the database (load_context) ----
    profile: dict                    # favorite/disliked genres, budget_cap, min_condition
    collection: list[dict]           # [{record_id, artist_norm, title_norm}]
    wantlist_artists: list[str]      # + taste score: artist affinity also counts the wantlist

    # ---- inventory search ----
    candidates: list[dict]           # copies found by search_inventory
    available_items: list[dict]      # after availability_filter (the only copies the LLM may feature)
    relax_attempts: int

    # ---- knowledge (record_info) ----
    knowledge_chunks: list[dict]
    catalog_details: list[dict]      # + catalog facts for records named in the question

    # ---- action awaiting confirmation (Human-in-the-Loop) ----
    pending_action: dict | None      # e.g. {"type": "reserve", "inventory_item_id": 57, ...}
    confirmed: bool | None
    action_result: dict | None       # + what execute_action did, for the answer

    # ---- answer + verification ----
    draft: dict | None               # AnswerDraft as dict
    answer_issues: list[str]
    answer_attempts: int
    answer: str


# Values every turn starts with. `messages` is appended by its reducer; `user_id` is set
# per request; `shown_item_ids` is deliberately NOT here: it must survive to the next turn.
TURN_RESET: dict[str, Any] = {
    'intent': '', 'request_constraints': {}, 'constraints': {}, 'refers_to_previous': False, 'reference_index': None,
    'reserve_requested': False,
    'profile': {}, 'collection': [], 'wantlist_artists': [],
    'candidates': [], 'available_items': [], 'relax_attempts': 0,
    'knowledge_chunks': [], 'catalog_details': [],
    'pending_action': None, 'confirmed': None, 'action_result': None,
    'draft': None, 'answer_issues': [], 'answer_attempts': 0, 'answer': '',
}
PERSISTENT_FIELDS = frozenset({'messages', 'user_id', 'shown_item_ids'})


def new_turn_input(text: str, user_id: int) -> dict[str, Any]:
    """The input for one new customer message: the message, who sent it, and all per-turn resets."""
    return {'messages': [HumanMessage(content=text)], 'user_id': user_id} | TURN_RESET
