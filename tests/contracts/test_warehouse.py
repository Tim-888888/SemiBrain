import pytest
from semibrain_business.warehouse import YieldQuery, compare_yields, generate


def test_generator_reproducibility_and_new_seed():
    a, b, c = generate(41), generate(41), generate(73)
    assert a["content_hash"] == b["content_hash"] != c["content_hash"]
    assert a["tables"]["test_results"] != c["tables"]["test_results"]
    rows = a["tables"]["test_results"]
    ids = {r["test_result_id"] for r in rows}
    assert len(rows) == len(ids)
    assert all(b["test_result_id"] in ids for b in a["tables"]["bin_results"])
    assert any(r["attempt_no"] > 1 for r in rows)
    assert any(not r["is_valid"] for r in rows)
    assert any((r["ingested_at"] - r["event_time"]).total_seconds() > 86400 for r in rows)
    assert all(
        "scenario" not in row and "oracle" not in row
        for table in a["tables"].values()
        for row in table
    )


def test_stage_and_time_must_be_explicit():
    with pytest.raises(ValueError):
        YieldQuery(
            lot_ids=["Lot-a"],
            stage="CP",
            program_version="CP-v1",
            start="2026-01-01",
            end="2026-01-02",
            as_of="2026-01-03",
        )
    assert generate(1)["tables"]["lots"][0]["lot_id"].startswith("DEV-Lot-")


def test_percentage_points_and_matching_are_not_percent_change():
    base = dict(
        metric_version="v1",
        stage="CP",
        program_version="CP-v1",
        metric="final",
        as_of="t",
        product_ids=["P1"],
        cohort_start="s",
        cohort_end="e",
    )
    assert compare_yields(base | {"value": 0.90}, base | {"value": 0.95})[
        "difference_percentage_points"
    ] == pytest.approx(-5)
    with pytest.raises(ValueError):
        compare_yields(base | {"value": 0.9}, base | {"value": 0.8, "stage": "FT"})
    with pytest.raises(ValueError):
        compare_yields(base | {"value": 0.9}, base | {"value": 0.8, "product_ids": ["P2"]})
