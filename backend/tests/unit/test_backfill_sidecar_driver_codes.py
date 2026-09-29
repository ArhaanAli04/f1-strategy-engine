"""Unit tests for scripts/backfill_sidecar_driver_codes.py's planning functions (no S3, no DB)."""

import pytest

from backend.scripts import backfill_sidecar_driver_codes as backfill

VER, ALB, OLD = "uuid-ver", "uuid-alb", "uuid-retired"
CODES = {VER: "VER", ALB: "ALB"}


def _sidecar(**extra: object) -> dict[str, object]:
    return {
        "holdout_mae": 0.55,
        "driver_id_to_code": {VER: 40, ALB: 0},
        "circuit_name_to_code": {"Monza": 9},
        **extra,
    }


@pytest.mark.unit
def test_translate_rekeys_the_table_by_driver_code_keeping_the_numbers() -> None:
    table, missing = backfill.translate_driver_table({VER: 40, ALB: 0}, CODES)

    assert table == {"VER": 40, "ALB": 0}
    assert missing == []


@pytest.mark.unit
def test_translate_reports_a_uuid_the_database_does_not_know() -> None:
    _, missing = backfill.translate_driver_table({VER: 40, OLD: 3}, CODES)
    assert missing == [OLD]


@pytest.mark.unit
def test_translate_refuses_one_code_with_two_numbers() -> None:
    with pytest.raises(ValueError, match="VER"):
        backfill.translate_driver_table({VER: 40, ALB: 0}, {VER: "VER", ALB: "VER"})


@pytest.mark.unit
def test_plan_adds_the_code_table() -> None:
    plan = backfill.plan_sidecar("tire_deg_medium.pkl", _sidecar(), CODES)

    assert plan.status == "add"
    assert plan.code_table == {"VER": 40, "ALB": 0}


@pytest.mark.unit
def test_plan_leaves_a_sidecar_that_already_has_the_same_table() -> None:
    sidecar = _sidecar(driver_code_to_code={"VER": 40, "ALB": 0})
    assert backfill.plan_sidecar("tire_deg_medium.pkl", sidecar, CODES).status == "current"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sidecar", "codes", "problem"),
    [
        (_sidecar(), {VER: "VER"}, "not in the source database"),
        (_sidecar(driver_code_to_code={"VER": 1}), CODES, "different driver_code_to_code"),
        ({"holdout_mae": 0.5}, CODES, "no driver_id_to_code"),
    ],
)
def test_plan_blocks_what_it_cannot_translate_safely(
    sidecar: dict[str, object], codes: dict[str, str], problem: str
) -> None:
    plan = backfill.plan_sidecar("tire_deg_medium.pkl", sidecar, codes)

    assert plan.status == "blocked"
    assert plan.problem is not None
    assert problem in plan.problem
