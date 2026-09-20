"""Durable read-only jobs. A terminal result is saved before any caller receives it."""

import json
import os
from datetime import timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from semibrain_common.runtime import (
    call,
    canonical,
    digest,
    expire_exhausted,
    failure,
    now,
    publish,
    transaction,
    uid,
)
from semibrain_contracts.models import ErrorInfo, SourceRef, ToolResult, assert_no_credentials
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from semibrain_business import warehouse as w
from semibrain_business.security import authorize_request, db
from semibrain_business.sql_policy import SQLInput, execute_query

router = APIRouter()


class SearchLots(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        default="",
        max_length=80,
        description="仅用于匹配用户明确给出的原始批次编号或编号片段；列出可用批次时留空。不是自然语言、产品或晶圆厂搜索。",
    )
    limit: int = Field(default=20, ge=1, le=50)


class LotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lot_id: str = Field(min_length=1, max_length=160)


REGISTRY = {}


def register(name, schema, function, description):
    if name in REGISTRY:
        raise ValueError("TOOL_NAME_CONFLICT")
    REGISTRY[name] = {"schema": schema, "function": function, "description": description}


@lru_cache(maxsize=1)
def engine():
    return w.engine_from_url(os.environ["SEMIBRAIN_WAREHOUSE_READ_URL"])


def read_rows(statement):
    from sqlalchemy import text

    with engine().connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        rows = [dict(r) for r in conn.execute(statement).mappings().fetchmany(201)]
    return {
        "rows": rows[:200],
        "row_count": min(len(rows), 200),
        "truncated": len(rows) > 200,
        "data_origin": "synthetic",
        "observed_at": now().isoformat(),
    }


def search_lots(form):
    return read_rows(
        select(w.lots)
        .where(w.lots.c.lot_id.contains(form.query, autoescape=True))
        .order_by(w.lots.c.lot_id)
        .limit(form.limit)
    )


def context(form):
    result = read_rows(select(w.lots).where(w.lots.c.lot_id == form.lot_id))
    result["test_scopes"] = read_rows(
        select(
            w.test_results.c.stage,
            w.test_results.c.program_version,
            func.min(w.test_results.c.event_time).label("first_event"),
            func.max(w.test_results.c.event_time).label("last_event"),
            func.max(w.test_results.c.ingested_at).label("data_watermark"),
        )
        .where(w.test_results.c.lot_id == form.lot_id)
        .group_by(w.test_results.c.stage, w.test_results.c.program_version)
    )["rows"]
    return result


def history(form):
    return read_rows(
        select(w.process_events)
        .where(w.process_events.c.lot_id == form.lot_id)
        .order_by(w.process_events.c.started_at)
        .limit(201)
    )


def alerts(form):
    return read_rows(
        select(w.fdc_events)
        .join(w.process_events)
        .where(w.process_events.c.lot_id == form.lot_id, w.fdc_events.c.is_alert.is_(True))
        .order_by(w.fdc_events.c.sample_time.desc())
        .limit(201)
    )


register("business.search_lots", SearchLots, search_lots, "查找合成演示批次，保留源批次编号。")
register(
    "business.get_lot_context",
    LotInput,
    context,
    "读取批次与阶段、测试程序、数据水位，供确定查询口径。",
)
register(
    "business.get_yield_summary",
    w.YieldQuery,
    lambda f: w.query_yield(engine(), f),
    "按批次、CP/FT、测试程序、时间、截至时间与首测/终测口径查询真实合成表，返回分子分母和单位。",
)
register("business.get_process_history", LotInput, history, "查询合成演示批次的工艺历史。")
register("business.get_fdc_alerts", LotInput, alerts, "查询合成演示批次的 FDC 告警和单位。")
register(
    "business.query",
    SQLInput,
    lambda f: execute_query(engine(), f),
    "执行受限单表 SELECT，服务端强制批次/时间窗口、只读账号和超时。",
)


@router.get("/internal/v1/tools")
def catalog(request: Request):
    claim = authorize_request(request, "business.catalog")
    return {
        "version": "p0-tools-v1",
        "data_origin": "synthetic",
        "tools": [
            {
                "name": name,
                "description": value["description"],
                "parameters": value["schema"].model_json_schema(),
            }
            for name, value in REGISTRY.items()
            if name in claim["allowed_ops"]
        ],
        "metric_version": w.METRIC_VERSION,
        "tables": {
            name: list(table.columns.keys())
            for name, table in w.metadata.tables.items()
            if name in {"lots", "test_results", "process_events", "defect_records"}
        },
    }


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logical_call_id: UUID
    tool: str = Field(max_length=120)
    arguments: dict[str, Any]


@router.post("/internal/v1/tool-jobs", status_code=202)
def submit(form: ToolInput, request: Request):
    if form.tool not in REGISTRY:
        failure("UNKNOWN_TOOL")
    claim = authorize_request(request, form.tool)
    if "demo" not in claim["resource_ids"]:
        failure("RESOURCE_SCOPE_DENIED", 403)
    try:
        args = REGISTRY[form.tool]["schema"].model_validate(form.arguments).model_dump(mode="json")
        assert_no_credentials(args)
        # SQL rejection occurs before accepting an executable job.
        if form.tool == "business.query":
            from semibrain_business.sql_policy import compile_query

            compile_query(SQLInput.model_validate(args))
    except ValueError:
        failure("TOOL_ARGUMENT_OR_POLICY_DENIED")
    key = str(form.logical_call_id)
    payload_hash = digest(
        canonical(
            {
                "tool": form.tool,
                "arguments": args,
                "subject": claim["subject_id"],
                "run": claim["run_id"],
                "policy": claim["policy_version"],
            }
        )
    )

    def accept(session):
        saved = db().tool_jobs.find_one({"_id": key}, session=session)
        if saved:
            if saved["payload_hash"] != payload_hash:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return saved
        row = {
            "_id": key,
            "logical_call_id": key,
            "tool": form.tool,
            "arguments": args,
            "subject_id": claim["subject_id"],
            "run_id": claim["run_id"],
            "auth_version": claim["auth_version"],
            "payload_hash": payload_hash,
            "status": "queued",
            "attempt": 0,
            "created_at": now(),
        }
        db().tool_jobs.insert_one(row, session=session)
        return row

    saved = transaction(accept)
    return {"job_id": key, "status": saved["status"]}


@router.get("/internal/v1/tool-jobs/{job_id}")
def status(job_id: str, request: Request):
    job = db().tool_jobs.find_one({"_id": job_id})
    if not job:
        failure("JOB_NOT_FOUND", 404)
    claim = authorize_request(request, job["tool"])
    if job["subject_id"] != claim["subject_id"] or job["run_id"] != claim["run_id"]:
        failure("JOB_NOT_FOUND", 404)
    return job.get("result") or {"job_id": job_id, "status": job["status"]}


def execute_one():
    def exhausted(row):
        result = ToolResult(
            job_id=row["_id"],
            logical_call_id=row["logical_call_id"],
            status="failed",
            error=ErrorInfo(
                code="ATTEMPTS_EXHAUSTED", message="查询重试次数已用完。", trace_id=row["_id"]
            ),
        )
        wire = result.model_dump(mode="json")
        return {"result": wire, "result_hash": digest(canonical(wire))}

    expire_exhausted(db(), "tool_jobs", "stream:business", "tool.completed", "tool", exhausted)
    fence = uid()
    job = db().tool_jobs.find_one_and_update(
        {
            "$or": [{"status": "queued"}, {"status": "running", "lease_until": {"$lt": now()}}],
            "attempt": {"$lt": 3},
        },
        {
            "$set": {
                "status": "running",
                "fence": fence,
                "lease_until": now() + timedelta(seconds=30),
            },
            "$inc": {"attempt": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not job:
        return False
    try:
        # Re-check current identity from non-secret references after any queue wait or retry.
        call(
            "conversation",
            "POST",
            "/internal/v1/authorization/check",
            json={
                "subject_id": job["subject_id"],
                "auth_version": job["auth_version"],
                "run_id": job["run_id"],
            },
        )
        tool = REGISTRY[job["tool"]]
        data = tool["function"](tool["schema"].model_validate(job["arguments"]))
        data = json.loads(canonical(data))
        assert_no_credentials(data)
        warnings = []
        assets = []
        if len(canonical(data).encode()) > 50000:
            from semibrain_business.knowledge import store_asset

            asset = store_asset(
                canonical(data).encode(),
                "application/json",
                job["subject_id"],
                "query-result.json",
                job_id=job["_id"],
            )
            assets = [asset["ref"]]
            data = {
                "row_count": data.get("row_count"),
                "truncated": True,
                "rows": [],
                "data_origin": "synthetic",
            }
            warnings.append("RESULT_STORED_AS_ASSET")
        data["result_state"] = result_state(data)
        source = SourceRef(
            source_id=job["_id"],
            source_version=w.METRIC_VERSION,
            content_hash=digest(canonical(data)),
            scope_ref="demo",
            kind="query",
            data_origin="synthetic",
            observed_at=now(),
            locator={"logical_call_id": job["logical_call_id"], "tool": job["tool"]},
        )
        result = ToolResult(
            job_id=job["_id"],
            logical_call_id=job["logical_call_id"],
            status="partial" if data["result_state"] == "partial" else "succeeded",
            data=data,
            source=source,
            artifact_refs=assets,
            warnings=warnings,
        )
    except Exception as exc:
        code = (
            "QUERY_TIMEOUT"
            if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "57014"
            else "TOOL_EXECUTION_FAILED"
        )
        result = ToolResult(
            job_id=job["_id"],
            logical_call_id=job["logical_call_id"],
            status="failed",
            error=ErrorInfo(
                code=code, message="查询未完成，请缩小范围或稍后重试。", trace_id=job["_id"]
            ),
        )
    wire = result.model_dump(mode="json")

    def complete(session):
        saved = db().tool_jobs.update_one(
            {"_id": job["_id"], "fence": fence, "status": "running", "lease_until": {"$gt": now()}},
            {
                "$set": {
                    "status": result.status,
                    "result": wire,
                    "result_hash": digest(canonical(wire)),
                    "completed_at": now(),
                }
            },
            session=session,
        )
        if saved.modified_count:
            publish(
                db(),
                "stream:business",
                "tool.completed",
                job["_id"],
                {"job_id": job["_id"], "status": result.status},
                session,
                aggregate_type="tool",
            )

    transaction(complete)
    return True


def result_state(data):
    if data.get("truncated"):
        return "partial"
    if data.get("row_count") == 0 or data.get("denominator") == 0:
        return "empty"
    return "complete"
