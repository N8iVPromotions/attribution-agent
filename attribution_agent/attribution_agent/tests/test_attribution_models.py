from datetime import datetime, timedelta

import pytest

from attribution_models import Touchpoint, allocate_credit


def _journey(count=4):
    start = datetime(2026, 1, 1)
    return [
        Touchpoint(
            touchpoint_id=f"tp-{idx}",
            occurred_at=start + timedelta(days=idx),
            channel=f"Channel {idx}",
            role="lead_creation" if idx == 1 else "touch",
        )
        for idx in range(count)
    ]


def test_last_touch_allocates_all_credit_to_final_touchpoint():
    result = allocate_credit(_journey(), "last_touch")
    assert [item.credit for item in result] == [0.0, 0.0, 0.0, 1.0]


def test_first_touch_allocates_all_credit_to_initial_touchpoint():
    result = allocate_credit(_journey(), "first_touch")
    assert [item.credit for item in result] == [1.0, 0.0, 0.0, 0.0]


def test_linear_splits_credit_evenly():
    result = allocate_credit(_journey(), "linear")
    assert [item.credit for item in result] == [0.25, 0.25, 0.25, 0.25]


def test_u_shape_weights_first_and_last_touch():
    result = allocate_credit(_journey(), "u_shape")
    credits = [item.credit for item in result]
    assert credits == pytest.approx([0.4, 0.1, 0.1, 0.4])
    assert sum(credits) == pytest.approx(1.0)


def test_w_shape_weights_first_lead_and_last_touch():
    result = allocate_credit(_journey(), "w_shape")
    credits = [item.credit for item in result]
    assert credits == pytest.approx([0.3, 0.3, 0.1, 0.3])
    assert sum(credits) == pytest.approx(1.0)


def test_w_shape_without_observed_lead_creation_degrades_to_u_shape():
    journey = [
        Touchpoint(
            touchpoint_id=f"tp-{idx}",
            occurred_at=datetime(2026, 1, 1) + timedelta(days=idx),
            channel=f"Channel {idx}",
        )
        for idx in range(4)
    ]
    credits = [item.credit for item in allocate_credit(journey, "w_shape")]
    assert credits == pytest.approx([0.4, 0.1, 0.1, 0.4])


def test_time_decay_sums_to_one_and_favors_recent_touchpoints():
    result = allocate_credit(_journey(), "time_decay")
    credits = [item.credit for item in result]
    assert sum(credits) == pytest.approx(1.0)
    assert result[-1].credit > result[0].credit
