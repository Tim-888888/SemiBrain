"""Opt-in checks against a disposable, explicitly configured foundation database."""

import copy
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from semibrain_business.warehouse import (
    YieldQuery,
    engine_from_url,
    generate,
    load_dataset,
    query_yield,
)
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database():
    if not os.getenv("SEMIBRAIN_WAREHOUSE_ADMIN_URL"):
        pytest.skip("Private foundation database not configured")
    owner = engine_from_url(os.environ["SEMIBRAIN_WAREHOUSE_ADMIN_URL"])
    reader = engine_from_url(os.environ["SEMIBRAIN_WAREHOUSE_READ_URL"])
    spec = importlib.util.spec_from_file_location(
        "reference", Path(os.environ["SEMIBRAIN_ORACLE_MODULE"])
    )
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    yield owner, reader, oracle.yield_oracle
    owner.dispose()
    reader.dispose()


def make_query(dataset, lot, stage="CP", metric="final", as_of=None):
    record = next(
        r for r in dataset["tables"]["test_results"] if r["lot_id"] == lot and r["stage"] == stage
    )
    start = min(r["event_time"] for r in dataset["tables"]["test_results"])
    return YieldQuery(
        lot_ids=[lot],
        stage=stage,
        program_version=record["program_version"],
        start=start,
        end=start + timedelta(days=2),
        as_of=as_of or start + timedelta(days=5),
        metric=metric,
    )


@pytest.mark.parametrize("seed", [41, 73, 129])
def test_sql_matches_independent_oracle(database, seed):
    owner, reader, oracle = database
    data = generate(seed, prefix=f"CHECK-{seed}")
    load_dataset(owner, data)
    assert load_dataset(owner, data) is False
    for lot in data["tables"]["lots"]:
        for stage in ["CP", "FT"]:
            for metric in ["first", "final"]:
                for days in [2, 5]:
                    q = make_query(
                        data,
                        lot["lot_id"],
                        stage,
                        metric,
                        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days),
                    )
                    expected = oracle(data["tables"]["test_results"], q)
                    actual = query_yield(reader, q)
                    assert {k: actual[k] for k in expected} == expected


def test_sql_empty_case_sensitive_and_read_only(database):
    owner, reader, _ = database
    data = generate(41, prefix="CHECK-41")
    q = make_query(data, data["tables"]["lots"][0]["lot_id"])
    assert query_yield(reader, q)["denominator"] == 24
    empty = query_yield(reader, q.model_copy(update={"lot_ids": [q.lot_ids[0].lower()]}))
    assert empty["denominator"] == 0 and empty["value"] is None
    assert empty["warnings"] == ["EMPTY_COHORT"]
    with pytest.raises(Exception):
        with reader.begin() as conn:
            conn.execute(text("UPDATE test_results SET pass_flag = false WHERE false"))
    with owner.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bin_results")).scalar_one() > 576


def test_load_rejects_modified_content(database):
    owner, _, _ = database
    data = copy.deepcopy(generate(41, prefix="CHECK-41"))
    data["tables"]["test_results"][0]["pass_flag"] = not data["tables"]["test_results"][0][
        "pass_flag"
    ]
    with pytest.raises(ValueError, match="DATASET_HASH_MISMATCH"):
        load_dataset(owner, data)


def test_hand_calculated_first_final_and_retest_after_cohort_window(database):
    owner, reader, oracle = database
    data = generate(809, prefix="HAND-CALC", lot_count=6, units_per_lot=4)
    lot = data["tables"]["lots"][3]["lot_id"]
    for row in data["tables"]["test_results"]:
        row["pass_flag"] = not (
            row["lot_id"] == lot
            and row["stage"] == "CP"
            and row["unit_id"].endswith("D0000")
            and row["attempt_no"] == 1
        )
    data["content_hash"] = hashlib.sha256(
        json.dumps(
            data["tables"], default=lambda d: d.isoformat(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    load_dataset(owner, data)
    q = make_query(data, lot)
    first_time = min(
        r["event_time"]
        for r in data["tables"]["test_results"]
        if r["lot_id"] == lot and r["stage"] == "CP"
    )
    q = q.model_copy(update={"start": first_time, "end": first_time + timedelta(minutes=10)})
    for metric, numerator in [("first", 3), ("final", 4)]:
        current = q.model_copy(update={"metric": metric})
        actual = query_yield(reader, current)
        assert (actual["numerator"], actual["denominator"], actual["value"]) == (
            numerator,
            4,
            numerator / 4,
        )
        assert oracle(data["tables"]["test_results"], current)["numerator"] == numerator


def test_live_http_reads_committed_table_changes(database):
    owner, reader, _ = database
    data = generate(41, prefix="CHECK-41")
    q = make_query(data, data["tables"]["lots"][0]["lot_id"], metric="first")
    base = os.environ["SEMIBRAIN_FOUNDATION_BASE_URL"]
    with httpx.Client(timeout=15) as client:
        assert (
            client.post(
                base + "/internal/v1/foundation/yield", json=q.model_dump(mode="json")
            ).status_code
            == 403
        )
        headers = {"Authorization": "Bearer " + os.environ["SEMIBRAIN_FOUNDATION_TOKEN"]}

        def call():
            response = client.post(
                base + "/internal/v1/foundation/yield",
                headers=headers,
                json=q.model_dump(mode="json"),
            )
            assert response.status_code == 200
            return response.json()

        before = call()
        row = next(
            r
            for r in data["tables"]["test_results"]
            if r["lot_id"] == q.lot_ids[0] and r["stage"] == "CP" and r["attempt_no"] == 1
        )
        try:
            with owner.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE test_results SET pass_flag=NOT pass_flag WHERE test_result_id=:id"
                    ),
                    {"id": row["test_result_id"]},
                )
            after = call()
            assert after["denominator"] == before["denominator"]
            assert abs(after["numerator"] - before["numerator"]) == 1
            assert after["numerator"] == query_yield(reader, q)["numerator"]
        finally:
            with owner.begin() as conn:
                conn.execute(
                    text("UPDATE test_results SET pass_flag=:value WHERE test_result_id=:id"),
                    {"id": row["test_result_id"], "value": row["pass_flag"]},
                )
