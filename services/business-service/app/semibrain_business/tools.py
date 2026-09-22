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
from semibrain_common.telemetry import Observation
from semibrain_contracts.models import ErrorInfo, SourceRef, ToolResult, assert_no_credentials
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from semibrain_business import warehouse as w
from semibrain_business.cancellation import CancellationScope, QueryCancelled, watch_engine
from semibrain_business.safe_fetch import WebError
from semibrain_business.security import authorize_request, db
from semibrain_business.sql_policy import SQLInput, execute_query
from semibrain_business.statistics import StatisticsInput, calculate
from semibrain_business.web_tools import WEB_TOOLS, configured

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
    return watch_engine(w.engine_from_url(os.environ["SEMIBRAIN_WAREHOUSE_READ_URL"]))


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
register(
    "business.statistics",
    StatisticsInput,
    None,
    "对本运行已成功查询的 job_ids 做均值、样本标准差、百分点差或分组比较。只能读已有授权结果，不接受自填数列、表达式或脚本；百分点比较必须同产品/阶段/程序/时间/口径。",
)

for _name, (_schema, _function, _description) in WEB_TOOLS.items():
    register(_name, _schema, _function, _description)


@router.get("/internal/v1/tools")
def catalog(request: Request):
    claim = authorize_request(request, "business.catalog")
    business_authorized = "demo" in claim["resource_ids"]
    return {
        "version": "p1-tools-v3",
        "data_origin": "synthetic",
        "business_access": {
            "resource_authorized": business_authorized,
            "reason": None if business_authorized else "RESOURCE_NOT_GRANTED",
        },
        "tools": [
            {
                "name": name,
                "description": value["description"],
                "parameters": value["schema"].model_json_schema(),
            }
            for name, value in REGISTRY.items()
            if name in claim["allowed_ops"]
            and (not name.startswith("web.") or configured())
            and (not name.startswith("business.") or business_authorized)
        ],
        "web_available": configured(),
        "metric_version": w.METRIC_VERSION,
        "tables": {
            name: list(table.columns.keys())
            for name, table in w.metadata.tables.items()
            if name in {"lots", "test_results", "process_events", "defect_records"}
        }
        if business_authorized
        else {},
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
    if form.tool.startswith("business.") and "demo" not in claim["resource_ids"]:
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
            "task_id": claim.get("task_id"),
            "trace_root_id": claim.get("trace_root_id"),
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
    claim = authorize_request(request, "lineage.check")
    if job["subject_id"] != claim["subject_id"] or job["run_id"] != claim["run_id"]:
        failure("JOB_NOT_FOUND", 404)
    return job.get("result") or {"job_id": job_id, "status": job["status"]}


@router.post("/internal/v1/tool-jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request):
    claim = authorize_request(request, "lineage.check")
    job = db().tool_jobs.find_one({"_id": job_id})
    if not job:
        # The agent records a logical call before submitting it. Cancelling the
        # parent can win that race; expose this only after authenticating the caller.
        return {"job_id": job_id, "status": "not_submitted", "accepted": False}
    if job["subject_id"] != claim["subject_id"] or job["run_id"] != claim["run_id"]:
        failure("JOB_NOT_FOUND", 404)

    def stop(session):
        current = db().tool_jobs.find_one({"_id": job_id}, session=session)
        if current["status"] in {"succeeded", "partial", "failed", "cancelled"}:
            return current["status"]
        if current["status"] == "queued":
            result = ToolResult(
                job_id=job_id, logical_call_id=job_id, status="cancelled"
            ).model_dump(mode="json")
            db().tool_jobs.update_one(
                {"_id": job_id, "status": "queued"},
                {
                    "$set": {
                        "status": "cancelled",
                        "result": result,
                        "result_hash": digest(canonical(result)),
                        "cancel_requested_at": now(),
                        "completed_at": now(),
                    }
                },
                session=session,
            )
            return "cancelled"
        db().tool_jobs.update_one(
            {"_id": job_id, "status": {"$in": ["running", "cancelling"]}},
            {"$set": {"status": "cancelling", "cancel_requested_at": now()}},
            session=session,
        )
        return "cancelling"

    return {"job_id": job_id, "status": transaction(stop)}


def execute_one():
    for expired in (
        db()
        .tool_jobs.find(
            {
                "lease_until": {"$lt": now()},
                "$or": [
                    {"status": "cancelling"},
                    {"status": "running", "tool": {"$regex": "^web\\."}},
                ],
            }
        )
        .limit(20)
    ):
        confirmed = bool(expired.get("query_stopped_at") and expired.get("cancel_requested_at"))
        terminal = ToolResult(
            job_id=expired["_id"],
            logical_call_id=expired["logical_call_id"],
            status="cancelled" if confirmed else "failed",
            error=None
            if confirmed
            else ErrorInfo(
                code="EXECUTION_CONFIRMATION_LOST",
                message="执行进程失联，无法确认外部执行结果，未自动重复请求。",
                trace_id=expired["_id"],
            ),
        ).model_dump(mode="json")

        def expire(session):
            changed = db().tool_jobs.update_one(
                {
                    "_id": expired["_id"],
                    "fence": expired["fence"],
                    "status": expired["status"],
                    "lease_until": {"$lt": now()},
                },
                {
                    "$set": {
                        "status": terminal["status"],
                        "result": terminal,
                        "result_hash": digest(canonical(terminal)),
                        "completed_at": now(),
                    }
                },
                session=session,
            )
            if changed.modified_count:
                publish(
                    db(),
                    "stream:business",
                    "tool.completed",
                    expired["_id"],
                    {"job_id": expired["_id"], "status": terminal["status"]},
                    session,
                    aggregate_type="tool",
                )

        transaction(expire)

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
            "$or": [
                {"status": "queued"},
                {
                    "status": "running",
                    "lease_until": {"$lt": now()},
                    "tool": {"$not": {"$regex": "^web\\."}},
                },
            ],
            "attempt": {"$lt": 3},
        },
        {
            "$set": {
                "status": "running",
                "fence": fence,
                "lease_until": now() + timedelta(seconds=90),
            },
            "$inc": {"attempt": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not job:
        return False
    span = Observation(
        job["run_id"],
        "tool." + job["tool"],
        kind="tool",
        service="business",
        parent_span_id=job.get("trace_root_id"),
        task_id=job.get("task_id"),
        logical_call_id=job["logical_call_id"],
        job_id=job["_id"],
        attempt=job["attempt"],
        tool=job["tool"],
    )
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
                "operation": job["tool"],
            },
        )
        tool = REGISTRY[job["tool"]]
        form = tool["schema"].model_validate(job["arguments"])
        with CancellationScope(db(), job):
            data = (
                calculate(form, job)
                if job["tool"] == "business.statistics"
                else tool["function"](form, job)
                if job["tool"].startswith("web.")
                else tool["function"](form)
            )
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
            kind="web" if job["tool"].startswith("web.") else "query",
            data_origin="public" if job["tool"].startswith("web.") else "synthetic",
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
            str(exc)
            if isinstance(exc, WebError)
            else "QUERY_TIMEOUT"
            if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "57014"
            else "TOOL_EXECUTION_FAILED"
        )
        result = ToolResult(
            job_id=job["_id"],
            logical_call_id=job["logical_call_id"],
            status="cancelled" if isinstance(exc, QueryCancelled) else "failed",
            error=ErrorInfo(
                code=code, message="工具未完成，请核对范围或稍后重试。", trace_id=job["_id"]
            ),
        )
    wire = result.model_dump(mode="json")
    span.end(
        status=wire["status"],
        error_code=wire.get("error", {}).get("code") if wire.get("error") else None,
    )

    def complete(session):
        final_wire = wire
        current = db().tool_jobs.find_one({"_id": job["_id"], "fence": fence}, session=session)
        if current and current.get("cancel_requested_at"):
            final_wire = ToolResult(
                job_id=job["_id"], logical_call_id=job["logical_call_id"], status="cancelled"
            ).model_dump(mode="json")
        saved = db().tool_jobs.update_one(
            {
                "_id": job["_id"],
                "fence": fence,
                "status": {"$in": ["running", "cancelling"]},
                "lease_until": {"$gt": now()},
            },
            {
                "$set": {
                    "status": final_wire["status"],
                    "result": final_wire,
                    "result_hash": digest(canonical(final_wire)),
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
                {"job_id": job["_id"], "status": final_wire["status"]},
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
