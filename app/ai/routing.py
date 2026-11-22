"""Routing: plain functions that read the state and name the next node (guide 10.5).

Pure: no I/O, no LLM. Every branch of the spec 4.2 diagram is decided here, by code.
"""

from langgraph.graph import END

from app.ai.state import StoreState
from app.services.relax import MAX_RELAX_ATTEMPTS

MAX_ANSWER_ATTEMPTS = 2          # compose → verify → (retry once) → fallback
RELAX_EXHAUSTED = MAX_RELAX_ATTEMPTS + 1

INTENT_NODES = {
    'find_record': 'search_inventory',
    'record_info': 'retrieve_knowledge',
    'reserve': 'prepare_reservation',
    'wantlist': 'prepare_wantlist_entry',
    'collection_help': 'collection_insights',
}


def route_after_understand(state: StoreState) -> str:
    return 'polite_refusal' if state.get('intent') not in INTENT_NODES else 'load_context'


def route_after_context(state: StoreState) -> str:
    return INTENT_NODES[state['intent']]


def route_after_filter(state: StoreState) -> str:
    if state.get('available_items'):
        return 'compose_answer'
    if state.get('relax_attempts', 0) < MAX_RELAX_ATTEMPTS:
        return 'relax_constraints'
    return 'offer_wantlist'


def route_after_relax(state: StoreState) -> str:
    """Nothing left to loosen → skip the pointless search and offer the wantlist."""
    return 'offer_wantlist' if state.get('relax_attempts', 0) >= RELAX_EXHAUSTED else 'search_inventory'


def route_after_prepare(state: StoreState) -> str:
    """Something to confirm → pause for the customer; otherwise (unclear, sold, limit) just answer."""
    return 'confirm_action' if state.get('pending_action') else 'compose_answer'


def route_after_verify(state: StoreState) -> str:
    if not state.get('answer_issues'):
        return END
    if state.get('answer_attempts', 0) < MAX_ANSWER_ATTEMPTS:
        return 'compose_answer'          # retry, with the issues in the prompt
    return 'fallback_template'
