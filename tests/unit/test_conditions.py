import pytest

from app.services.conditions import CONDITION_ORDER, condition_rank, meets_minimum


def test_order_worst_to_best():
    assert CONDITION_ORDER == ['P', 'F', 'G', 'G+', 'VG', 'VG+', 'NM', 'M']
    assert condition_rank('P') < condition_rank('VG') < condition_rank('M')


@pytest.mark.parametrize(
    ('grade', 'minimum', 'ok'),
    [
        ('NM', 'VG+', True),
        ('VG+', 'VG+', True),   # equal is enough
        ('VG', 'VG+', False),
        ('M', 'NM', True),
        ('P', None, True),      # no minimum
    ],
)
def test_meets_minimum(grade, minimum, ok):
    assert meets_minimum(grade, minimum) is ok


def test_unknown_grade_raises():
    with pytest.raises(ValueError):
        condition_rank('EX')
