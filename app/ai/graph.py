"""The LangGraph graph (spec 4.2), assembled from the nodes (10.3) and routing (10.5).

Built in the guide's order (10.4), each stage tested in tests/graph/test_graph.py:
  A  happy path        understand → load_context → search → filter → compose → END, + polite_refusal
  B  verification      compose → verify → (retry once) → fallback_template
  C  relax loop        filter → relax → search … → offer_wantlist
  D  knowledge         record_info → retrieve_knowledge → compose
  E  collection        collection_help → collection_insights → compose
  F  Human-in-the-Loop prepare_* / offer_wantlist → confirm_action (interrupt) → execute_action → compose

The checkpointer is passed in: InMemorySaver in scripts/tests, AsyncPostgresSaver in the app
(Phase 11). Without a checkpointer the confirmation interrupt can't resume.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer

from app.ai.deps import Deps
from app.ai.nodes import Nodes
from app.ai.routing import (
    route_after_context,
    route_after_filter,
    route_after_prepare,
    route_after_relax,
    route_after_understand,
    route_after_verify,
)
from app.ai.state import StoreState

NODE_NAMES = [
    'understand_request', 'polite_refusal', 'load_context',
    'search_inventory', 'availability_filter', 'relax_constraints', 'offer_wantlist',
    'retrieve_knowledge', 'collection_insights',
    'prepare_reservation', 'prepare_wantlist_entry', 'confirm_action', 'execute_action',
    'compose_answer', 'verify_answer', 'fallback_template',
]


def build_graph(deps: Deps, checkpointer: Checkpointer = None):
    nodes = Nodes(deps)
    g = StateGraph(StoreState)
    for name in NODE_NAMES:
        g.add_node(name, getattr(nodes, name))

    # A: entry and routing by intent
    g.add_edge(START, 'understand_request')
    g.add_conditional_edges('understand_request', route_after_understand, ['polite_refusal', 'load_context'])
    g.add_edge('polite_refusal', END)
    g.add_conditional_edges('load_context', route_after_context,
                            ['search_inventory', 'retrieve_knowledge', 'prepare_reservation',
                             'prepare_wantlist_entry', 'collection_insights'])

    # A + C: search, store rules, relax loop, wantlist offer
    g.add_edge('search_inventory', 'availability_filter')
    g.add_conditional_edges('availability_filter', route_after_filter,
                            ['compose_answer', 'relax_constraints', 'offer_wantlist'])
    g.add_conditional_edges('relax_constraints', route_after_relax, ['search_inventory', 'offer_wantlist'])

    # D + E: information
    g.add_edge('retrieve_knowledge', 'compose_answer')
    g.add_edge('collection_insights', 'compose_answer')

    # F: actions → confirmation (only if there is something to confirm) → execute
    for prepare in ('offer_wantlist', 'prepare_reservation', 'prepare_wantlist_entry'):
        g.add_conditional_edges(prepare, route_after_prepare, ['confirm_action', 'compose_answer'])
    g.add_edge('confirm_action', 'execute_action')      # execute_action handles "no" (writes nothing)
    g.add_edge('execute_action', 'compose_answer')

    # B: every answer is verified; two failures → template answer
    g.add_edge('compose_answer', 'verify_answer')
    g.add_conditional_edges('verify_answer', route_after_verify, ['compose_answer', 'fallback_template', END])
    g.add_edge('fallback_template', END)

    return g.compile(checkpointer=checkpointer)
