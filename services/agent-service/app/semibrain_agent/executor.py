"""Authorized, idempotent observations and evidence handles for the single Agent."""

import json
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import canonical, digest, now
from semibrain_common.text_window import read_window
from semibrain_common.tool_errors import SANDBOX_ARGUMENT_ERRORS
from semibrain_contracts.models import EvidenceRef, SourceRef, assert_no_credentials

from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.query_contract import QueryScopeError, bind_query


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=4, ge=1, le=6)


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    version: UUID
    offset: int = Field(default=0, ge=0, le=1000000)
    length: int = Field(default=5000, ge=500, le=8000)


class EvidenceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: UUID
    offset: int = Field(default=0, ge=0, le=1000000)
    length: int | None = Field(default=None, ge=200, le=12000)
    query: str | None = Field(default=None, min_length=1, max_length=200)


LOCAL_TOOLS = {
    "knowledge.search": (
        Search,
        "检索当前授权知识范围。结果包含有版本的原文片段与引用；不是网络搜索。",
    ),
    "knowledge.read": (
        Read,
        "读取目录或检索结果给出的文档 ID 与当前版本，按字符位置续读，不猜编号。",
    ),
    "evidence.read": (
        EvidenceRead,
        "读取本运行已保存证据，可用offset/length取原文区段或query在留存原文中精确定位。网页未抓取部分无法恢复，请用web.read续读快照。",
    ),
}


def extend_catalog(catalog):
    extra = [
        {"name": name, "description": spec[1], "parameters": spec[0].model_json_schema()}
        for name, spec in LOCAL_TOOLS.items()
    ]
    return {**catalog, "tools": catalog["tools"] + extra}


def wire_tools(catalog):
    names, tools = {}, []
    for item in catalog["tools"]:
        name = item["name"].replace(".", "__")
        if name in names:
            raise ValueError("TOOL_NAME_COLLISION")
        names[name] = item["name"]
        tools.append(
            {
                "type": "function",
                "name": name,
                "description": item["description"],
                "parameters": item["parameters"],
                "strict": False,
            }
        )
    return tools, names


class ToolExecutor:
    def __init__(self, harness, client):
        self.harness, self.client = harness, client
        self.db, self.run_id = harness.db, harness.run_id

    def evidence(self):
        records = list(
            self.db.evidence.find({"run_id": self.run_id, "marker": {"$exists": True}, "body_expired_at": {"$exists": False}}).sort(
                "ordinal", 1
            )
        )
        refs = list(
            dict.fromkeys(ref for record in records for ref in record.get("lineage_refs", []))
        )
        self.client.request("POST", "/internal/v1/lineage/check", json={"refs": refs})
        return records

    def register(
        self,
        *,
        source,
        content,
        title,
        refs,
        asset_id=None,
        job_id=None,
        location=None,
        limitations=None,
    ):
        identity = str(
            uuid5(
                NAMESPACE_URL,
                self.run_id
                + ":"
                + digest(
                    canonical(
                        {
                            "source": {k: v for k, v in source.items() if k != "observed_at"},
                            "content": content,
                        }
                    )
                ),
            )
        )
        existing = self.db.evidence.find_one({"_id": identity, "run_id": self.run_id})
        if existing:
            self.client.request(
                "POST", "/internal/v1/lineage/check", json={"refs": existing["lineage_refs"]}
            )
            return existing
        count = self.db.evidence.count_documents(
            {"run_id": self.run_id, "marker": {"$exists": True}}
        )
        if count >= 50:
            raise BudgetExhausted("EVIDENCE_BUDGET_EXHAUSTED")
        value = EvidenceRef(
            evidence_id=identity,
            run_id=self.run_id,
            source=SourceRef.model_validate(source),
            limitations=limitations or [],
        ).model_dump(mode="json")
        record = {
            **value,
            "marker": str(count + 1),
            "ordinal": count + 1,
            "content": content,
            "title": title,
            "lineage_refs": refs,
            "lineage_ref": refs[0] if refs else None,
            "asset_id": asset_id,
            "job_id": job_id,
            "location": location or {},
        }
        self.harness.save_record("evidence", identity, record)
        return {"_id": identity, **record}

    @staticmethod
    def observation(record):
        return {
            key: record.get(key)
            for key in (
                "evidence_id",
                "marker",
                "content",
                "title",
                "lineage_ref",
                "source",
                "limitations",
                "job_id",
            )
        }

    def documents(self, chunks):
        evidence = []
        for chunk in chunks:
            source = SourceRef(
                source_id=chunk["document_id"],
                source_version=chunk["version"],
                content_hash=chunk["content_hash"],
                scope_ref="demo",
                kind="image" if chunk.get("media_type", "").startswith("image/") else "document",
                data_origin=chunk["data_origin"],
                observed_at=now(),
                locator=chunk["location"],
            ).model_dump(mode="json")
            saved = self.register(
                source=source,
                content=chunk["text"],
                title=chunk["title"],
                refs=[chunk["lineage_ref"]],
                asset_id=chunk["asset_id"],
                location=chunk["location"],
                limitations=["PARTIAL_DOCUMENT"] if chunk.get("truncated") else [],
            )
            evidence.append(
                {
                    **self.observation(saved),
                    "next_offset": chunk.get("next_offset"),
                    "document_id": chunk["document_id"],
                    "version": chunk["version"],
                }
            )
        return evidence

    def execute(self, name, raw_arguments, logical_id):
        self.harness.check()
        saved = self.db.observations.find_one({"_id": logical_id, "run_id": self.run_id})
        if saved and saved.get("observation"):
            self.evidence()
            return saved["observation"]
        try:
            args = json.loads(raw_arguments)
            if not isinstance(args, dict):
                raise ValueError("TOOL_OBJECT_REQUIRED")
            assert_no_credentials(args)
            args = bind_query(name, args, getattr(self, "intent", {}))
            reusable = {"business.search_lots", "business.get_yield_summary",
                        "business.get_lot_context", "business.get_process_history",
                        "business.get_fdc_alerts", "business.statistics"}
            if name in reusable:
                previous = self.db.observations.find_one({
                    "run_id": self.run_id, "observation.tool": name,
                    "observation.arguments": args, "observation.status": "succeeded",
                    "observation.reused_from": {"$exists": False},
                })
                if previous:
                    self.evidence()  # Reauthorize sources before reusing within this immutable run.
                    observation = {**previous["observation"], "call_ref": logical_id,
                                   "reused_from": previous["_id"],
                                   "notice": "复用本运行相同范围的成功查询，未再次访问业务数据。"}
                    self.harness.save_record("observations", logical_id, {"observation": observation})
                    return observation
            self.harness.reserve_tool(logical_id, name)
            if name in LOCAL_TOOLS:
                args = LOCAL_TOOLS[name][0].model_validate(args).model_dump(mode="json")
            if name == "knowledge.search":
                result = self.client.request(
                    "POST", "/internal/v1/retrieval/search", json=args, timeout=70
                )
                observation = {
                    "status": "succeeded",
                    "evidence": self.documents(result["evidence"]),
                    "retrieval": result["retrieval"],
                }
            elif name == "knowledge.read":
                result = self.client.request("POST", "/internal/v1/knowledge/read", json=args)
                observation = {
                    "status": "succeeded",
                    "evidence": self.documents(result["evidence"]),
                }
            elif name == "evidence.read":
                records = {item["evidence_id"]: item for item in self.evidence()}
                if args["evidence_id"] not in records:
                    raise ValueError("EVIDENCE_NOT_IN_RUN")
                record = self.observation(records[args["evidence_id"]])
                value = record.get("content")
                field = next((k for k in ("text", "stdout") if isinstance(value, dict) and isinstance(value.get(k), str)), None)
                if (args.get("length") or args.get("query") or args.get("offset")) and (isinstance(value, str) or field):
                    window = read_window(value[field] if field else value, args["offset"], args.get("length") or 7000, args.get("query"))
                    record["content"] = {**value, field: window["text"]} if field else window["text"]
                    record["projection"] = {k: v for k, v in window.items() if k != "text"}
                    if field == "text":
                        start = value.get("offset", 0)
                        record["content"].update(offset=start + window["offset"],
                            next_offset=start + window["next_offset"] if window["next_offset"] is not None else value.get("next_offset"))
                observation = {
                    "status": "succeeded",
                    "evidence": [record],
                }
            else:
                result = self.client.tool(
                    name,
                    args,
                    logical_id=logical_id,
                    guard=self.harness.check,
                    timeout=65 if name.startswith(("web.", "sandbox.", "vision.")) else 30,
                )
                observation = {
                    "status": result["status"],
                    "job_id": result.get("job_id"),
                    "warnings": result.get("warnings", []),
                    "error": result.get("error"),
                }
                if name in {"web.search", "vision.inspect"}:
                    self.harness.settle_external_tool(
                        logical_id, (result.get("data") or {}).get("usage")
                    )
                if result["status"] in {"succeeded", "partial"}:
                    if name == "web.search":
                        observation["data"] = result["data"]
                        observation.update(call_ref=logical_id, tool=name, arguments=args)
                        self.harness.save_record(
                            "observations", logical_id, {"observation": observation}
                        )
                        return observation
                    refs = ["query:" + result["job_id"] + ":" + digest(canonical(result))]
                    refs += result["data"].get("lineage_refs", [])
                    saved = self.register(
                        source=result["source"],
                        content=result["data"],
                        title=(
                            result["data"].get("title") or result["data"].get("url") or "公开网页"
                        )
                        if name.startswith("web.")
                        else "图片观察" if name == "vision.inspect"
                        else "沙箱分析 · " + name if name.startswith("sandbox.")
                        else "合成演示数据 · " + name,
                        refs=refs,
                        job_id=result["job_id"],
                        asset_id=result["data"].get("asset_id")
                        or (
                            result["artifact_refs"][0]["asset_id"]
                            if result["artifact_refs"]
                            else None
                        ),
                        location={"url": result["data"]["url"]}
                        if name.startswith("web.")
                        else None,
                        limitations=result.get("warnings", [])
                        + (["PARTIAL_RESULT"] if result["status"] == "partial" else []),
                    )
                    observation["evidence"] = [self.observation(saved)]
            observation.update(call_ref=logical_id, tool=name, arguments=args)
        except (RunStopped, BudgetExhausted):
            raise
        except (ValueError, HTTPException, TimeoutError) as exc:
            if name in {"web.search", "vision.inspect"}:
                self.harness.settle_external_tool(logical_id, None)
            code = (
                "QUERY_SCOPE_MISMATCH"
                if isinstance(exc, QueryScopeError)
                else
                exc.detail.get("code", "TOOL_REQUEST_FAILED")
                if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
                else "TOOL_DEADLINE"
                if isinstance(exc, TimeoutError)
                else "TOOL_ARGUMENT_OR_EXECUTION_FAILED"
            )
            observation = {
                "status": "failed",
                "error": {"code": code, **({"message": str(exc)} if isinstance(exc, QueryScopeError)
                          else {"message": SANDBOX_ARGUMENT_ERRORS[code]}
                          if code in SANDBOX_ARGUMENT_ERRORS else {})},
                "tool": name,
                "call_ref": logical_id,
                "evidence": [],
            }
        self.harness.check()
        self.harness.save_record("observations", logical_id, {"observation": observation})
        # Terminal observation journal is immutable and can be replayed after a checkpoint gap.
        return self.db.observations.find_one({"_id": logical_id, "run_id": self.run_id})[
            "observation"
        ]
