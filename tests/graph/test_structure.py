"""The graph's wiring matches spec 4.2 (plus three documented additions). Guards against drift."""

from app.ai.graph import NODE_NAMES
from scripts.draw_graph import _NoLLM, build_graph, mermaid
from app.ai.deps import Deps

# (source, target, conditional?) — the spec 4.2 diagram as edges
SPEC_EDGES = {
    ('__start__', 'understand_request', False),
    ('understand_request', 'polite_refusal', True), ('understand_request', 'load_context', True),
    ('polite_refusal', '__end__', False),
    ('load_context', 'search_inventory', True), ('load_context', 'retrieve_knowledge', True),
    ('load_context', 'prepare_reservation', True), ('load_context', 'prepare_wantlist_entry', True),
    ('load_context', 'collection_insights', True),
    ('search_inventory', 'availability_filter', False),
    ('availability_filter', 'relax_constraints', True), ('availability_filter', 'offer_wantlist', True),
    ('availability_filter', 'compose_answer', True),
    ('relax_constraints', 'search_inventory', True),
    ('offer_wantlist', 'confirm_action', True),
    ('retrieve_knowledge', 'compose_answer', False),
    ('prepare_reservation', 'confirm_action', True), ('prepare_wantlist_entry', 'confirm_action', True),
    ('execute_action', 'compose_answer', False),
    ('collection_insights', 'compose_answer', False),
    ('compose_answer', 'verify_answer', False),
    ('verify_answer', 'compose_answer', True), ('verify_answer', 'fallback_template', True),
    ('verify_answer', '__end__', True), ('fallback_template', '__end__', False),
}
# Deliberate differences (see build_guide 10.4 "As built"):
ADDITIONS = {
    ('relax_constraints', 'offer_wantlist', True),        # nothing left to loosen: no pointless search
    ('offer_wantlist', 'compose_answer', True),           # nothing to confirm (no artist)
    ('prepare_reservation', 'compose_answer', True),      # unclear / sold / limit: just answer
    ('prepare_wantlist_entry', 'compose_answer', True),   # no artist: ask for it
    ('confirm_action', 'execute_action', False),          # "no" also goes through execute (writes nothing)
}


def test_edges_match_spec_plus_documented_additions():
    drawn = build_graph(Deps(llm=_NoLLM(), repo=None)).get_graph()
    edges = {(e.source, e.target, e.conditional) for e in drawn.edges}
    assert edges == SPEC_EDGES | ADDITIONS


def test_every_node_is_in_the_diagram():
    text = mermaid()
    assert all(f'{name}({name})' in text for name in NODE_NAMES)
