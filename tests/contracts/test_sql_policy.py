import pytest
from semibrain_business.sql_policy import SQLInput, compile_query


def form(sql):
    return SQLInput(
        sql=sql, lot_ids=["DEMO-X"], start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE lots",
        "UPDATE lots SET family_id='x'",
        "SELECT lot_id FROM lots; DELETE FROM lots",
        "SELECT pg_sleep(10) FROM lots",
        "SELECT * FROM pg_authid",
        "SELECT password FROM lots",
        "SELECT lot_id INTO copied FROM lots",
        "SELECT lot_id FROM lots FOR UPDATE",
        "SELECT a.lot_id FROM lots a CROSS JOIN lots b",
        "SELECT * FROM lots",
        "SELECT lot_id FROM other.lots",
        "SELECT lot_id FROM lots OFFSET 10000000",
        "SELECT lot_id FROM lots LIMIT 10000",
        "SELECT generate_series(1,10000000) FROM lots",
    ],
)
def test_denies_mutation_exfiltration_and_unbounded_shapes(sql):
    with pytest.raises(ValueError):
        compile_query(form(sql))


def test_server_scan_scope_survives_user_or_and_preserves_limit():
    sql, params = compile_query(
        form("SELECT lot_id FROM lots WHERE lot_id='OTHER' OR 1=1 LIMIT 10")
    )
    assert 'FROM (SELECT * FROM "lots" WHERE lot_id = ANY(:scope_lots)) AS lots' in sql
    assert sql.endswith("LIMIT 10")
    assert params["scope_lots"] == ["DEMO-X"]


def test_fact_scan_contains_independent_time_partition():
    sql, _ = compile_query(form("SELECT COUNT(*) AS total FROM test_results"))
    assert '"event_time" >= :scope_start' in sql
    assert '"event_time" < :scope_end' in sql
    assert "ANY(:scope_lots)" in sql


def test_scan_window_cannot_be_unbounded():
    with pytest.raises(ValueError):
        SQLInput(
            sql="SELECT lot_id FROM lots",
            lot_ids=["x"],
            start="2020-01-01T00:00:00Z",
            end="2026-01-01T00:00:00Z",
        )
