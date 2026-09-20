"""A deliberately small SQL dialect with server-injected resource scans and real PG limits."""

from datetime import timedelta

import sqlglot
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlglot import exp

from semibrain_business.warehouse import metadata

SCANS = {
    "lots": None,
    "test_results": "event_time",
    "process_events": "started_at",
    "defect_records": "event_time",
}
FUNCTIONS = {"COUNT", "SUM", "AVG", "MIN", "MAX", "ROUND", "COALESCE", "ABS"}


class SQLInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=6000)
    lot_ids: list[str] = Field(min_length=1, max_length=20)
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def window(self):
        if not timedelta(0) < self.end - self.start <= timedelta(days=31):
            raise ValueError("Scan window must be between zero and 31 days")
        return self


def compile_query(form: SQLInput):
    try:
        statements = sqlglot.parse(form.sql, read="postgres")
    except sqlglot.errors.ParseError:
        raise ValueError("SQL_PARSE_FAILED") from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("SELECT_ONLY")
    tree = statements[0]
    if any(
        tree.find(kind)
        for kind in (exp.Into, exp.Lock, exp.With, exp.Join, exp.Subquery, exp.Union, exp.Offset)
    ):
        raise ValueError("SQL_SHAPE_DENIED")
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1 or tables[0].name not in SCANS or tables[0].db or tables[0].catalog:
        raise ValueError("TABLE_DENIED")
    table = tables[0]
    allowed = set(metadata.tables[table.name].columns.keys())
    aliases = {projection.alias for projection in tree.expressions if projection.alias}
    for column in tree.find_all(exp.Column):
        if column.name not in allowed | aliases:
            raise ValueError("COLUMN_DENIED")
        if column.table and column.table not in {table.name, table.alias}:
            raise ValueError("COLUMN_SCOPE_DENIED")
    for func in tree.find_all(exp.Func):
        if isinstance(func, (exp.And, exp.Or, exp.Not)):
            continue
        name = func.name.upper() if isinstance(func, exp.Anonymous) else func.sql_name().upper()
        if name not in FUNCTIONS:
            raise ValueError("FUNCTION_DENIED")
    if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
        raise ValueError("EXPLICIT_COLUMNS_REQUIRED")
    if tree.find(exp.Placeholder) or tree.find(exp.Parameter) or tree.find(exp.Command):
        raise ValueError("USER_PARAMETER_DENIED")
    limit = tree.args.get("limit")
    if limit and (
        not isinstance(limit.expression, exp.Literal)
        or not limit.expression.is_int
        or not 1 <= int(limit.expression.this) <= 500
    ):
        raise ValueError("LIMIT_DENIED")
    tree = tree.limit(int(limit.expression.this) if limit else 501, copy=False)
    # All underlying scans are narrowed outside the supplied query. OR/NOT in user SQL cannot
    # remove these predicates; LIMIT only bounds returned rows, never the physical scan scope.
    scoped = f'SELECT * FROM "{table.name}" WHERE lot_id = ANY(:scope_lots)'
    field = SCANS[table.name]
    if field:
        scoped += f' AND "{field}" >= :scope_start AND "{field}" < :scope_end'
    replacement = sqlglot.parse_one(scoped, read="postgres").subquery(
        alias=table.alias or table.name
    )
    table.replace(replacement)
    sql = tree.sql(dialect="postgres")
    for name in ("scope_lots", "scope_start", "scope_end"):
        sql = sql.replace("%(" + name + ")s", ":" + name)
    return sql, {
        "scope_lots": form.lot_ids,
        "scope_start": form.start,
        "scope_end": form.end,
    }


def execute_query(engine, form: SQLInput):
    sql, params = compile_query(form)
    with engine.connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        conn.execute(text("SET LOCAL lock_timeout = '500ms'"))
        plan = conn.execute(text("EXPLAIN (FORMAT JSON) " + sql), params).scalar_one()[0]["Plan"]
        if plan["Total Cost"] > 100000 or plan["Plan Rows"] > 100000:
            raise ValueError("SCAN_BUDGET_EXCEEDED")
        rows = [dict(row) for row in conn.execute(text(sql), params).mappings().fetchmany(501)]
    return {
        "rows": rows[:500],
        "row_count": min(len(rows), 500),
        "truncated": len(rows) > 500,
        "data_origin": "synthetic",
        "scan_lot_ids": form.lot_ids,
        "window": {"start": form.start.isoformat(), "end": form.end.isoformat()},
    }
