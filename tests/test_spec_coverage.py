"""The 20 minimum tests from spec section 12 exist under their exact names.

A rename or deletion would otherwise go unnoticed until the defense. Where each one lives:

  unit   test_availability  sold/reserved, owned, budget_cap, min_condition filters
         test_normalize     name normalization
         test_taste         taste score
         test_relax         relax never touches hard rules (+ loop limit, pure part)
         test_verify        unavailable item, invented price
  graph  test_graph         relax loop limit, verify fallback, confirmation, interrupt/resume
         test_nodes         reference index resolved by code
  api    test_reservations  double reservation → 409, limit of 3
         test_inventory     wantlist match on new inventory, staff-only CRUD
         test_graph_db      off-topic refusal writes nothing (real DB)
         test_chat          someone else's chat → 404
"""

import ast
from pathlib import Path

SPEC_TESTS = {
    'test_filter_drops_sold_and_reserved', 'test_filter_drops_owned_records', 'test_filter_budget_cap_is_hard',
    'test_filter_min_condition', 'test_name_normalization', 'test_taste_score', 'test_relax_never_touches_hard_rules',
    'test_relax_loop_limit', 'test_verify_rejects_unavailable_item', 'test_verify_rejects_invented_price',
    'test_verify_fallback_after_two_failures', 'test_reference_index_resolved_by_code',
    'test_reservation_requires_confirmation', 'test_interrupt_and_resume', 'test_double_reservation_returns_409',
    'test_reservation_limit', 'test_wantlist_match_on_new_inventory', 'test_off_topic_refusal',
    'test_user_cannot_read_others_chat', 'test_staff_only_inventory_crud',
}


def test_all_spec_tests_exist():
    defined = set()
    for path in Path(__file__).parent.rglob('test_*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
    assert len(SPEC_TESTS) == 20
    assert SPEC_TESTS - defined == set()
