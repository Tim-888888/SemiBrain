"""A bounded LangGraph transition machine with explicit fenced checkpoint commits."""

import json
import re
import time
from dataclasses import asdict
from datetime import timedelta
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from semibrain_common.runtime import call as service_call
from semibrain_common.runtime import canonical, digest, now, publish, transaction, uid
from semibrain_common.telemetry import Observation
from semibrain_contracts.models import CitationBinding, Report

from semibrain_agent.checkpoints import GRAPH_VERSION, STATE_VERSION, Checkpoints
from semibrain_agent.client import BusinessClient
from semibrain_agent.executor import ToolExecutor, extend_catalog, wire_tools
from semibrain_agent.harness import BudgetExhausted, Harness, RunStopped, estimate_reservation
from semibrain_agent.partial import partial_answer
from semibrain_agent.prompts import (
    Intent,
    PromptAssembler,
    Review,
    RoutePolicy,
    compact_messages,
    parse_control,
    redact_preview,
)
from semibrain_agent.provider import (
    ModelError,
    ModelProfile,
    ModelTurn,
    ProviderAdapter,
    profile_for,
)


class GraphState(TypedDict):
    payload: dict


class Investigator:
    def __init__(self, run, fence, context, notify):
        self.run, self.context, self.notify = run, context, notify
        self.harness = Harness(run["_id"], fence)
        self.db = self.harness.db
        self.harness.initialize()
        self.client = BusinessClient(
            run["_id"], context["task_id"], context["input"]["input_revision"]
        )
        self.executor = ToolExecutor(self.harness, self.client)
        catalog = extend_catalog(self.client.request("GET", "/internal/v1/tools"))
        catalog["tools"] = [
            tool
            for tool in catalog["tools"]
            if context["input"]["allow_web"] or not tool["name"].startswith("web.")
        ]
        documents = self.client.request("GET", "/internal/v1/knowledge/documents")["items"]
        self.attachments = (
            self.client.request("GET", "/internal/v1/attachments/context")["items"]
            if context["input"]["attachment_refs"]
            else []
        )
        sources = {
            "explicit_selection": bool(context["input"]["resource_restrictions"]),
            "documents": [
                {"document_id": doc["id"], "version": doc["active_version"], "title": doc["title"]}
                for doc in documents
                if doc.get("active_version")
            ][:60],
            "directory_truncated": len(documents) > 60,
        }
        if not sources["documents"] and not self.attachments:
            catalog["tools"] = [
                tool for tool in catalog["tools"] if not tool["name"].startswith("knowledge.")
            ]
        self.catalog = catalog
        self.wire, self.names = wire_tools(catalog)
        self.prompts = PromptAssembler(context, catalog, sources, self.attachments)
        self.checkpoints = Checkpoints(self.harness, context["task_id"], run["attempt"])
        self.state = self.checkpoints.restore()
        runtime_models = {
            role: profile_for(role).snapshot()
            for role in ("understanding", "investigator", "reviewer")
        }
        self.bundle = run.get("version_bundle") or {
            "graph_version": GRAPH_VERSION,
            "state_version": STATE_VERSION,
            **self.prompts.snapshot(),
            "route_version": RoutePolicy().version,
            "models": runtime_models,
            "metric_version": catalog.get("metric_version"),
        }
        if (
            self.bundle["graph_version"] != GRAPH_VERSION
            or self.bundle["tool_version"] != catalog.get("version")
            or self.bundle["prompt_version"] != self.prompts.snapshot()["prompt_version"]
            or self.bundle["models"] != runtime_models
        ):
            raise RuntimeError("RUN_VERSION_INCOMPATIBLE")
        self.notify(
            {
                "version_bundle": self.bundle,
                "strategy": "single_agent",
                "model_origin": "api_simulated",
                "progress": "正在准备智能调查",
            }
        )
        builder = StateGraph(GraphState)
        phases = ("understand", "model", "tools", "finalize", "review", "revise")
        for phase in phases:
            builder.add_node(phase, self.node(phase))
            builder.add_edge(phase, END)
        builder.add_conditional_edges(
            START,
            lambda value: value["payload"]["phase"],
            {phase: phase for phase in phases},
        )
        self.graph = builder.compile()

    def node(self, phase):
        def execute(value):
            self.harness.check()
            current = service_call(
                "conversation", "GET", "/internal/v1/runs/" + self.run["_id"] + "/context"
            ).json()
            if current.get("cancel_requested"):
                from semibrain_agent.control import request_cancel

                request_cancel(self.run["_id"], "gateway-control")
                raise RunStopped("RUN_CANCELLED")
            if not current.get("web_allowed", False) and self.context["input"]["allow_web"]:
                self.context["input"]["allow_web"] = False
                self.catalog["tools"] = [
                    tool for tool in self.catalog["tools"] if not tool["name"].startswith("web.")
                ]
                self.wire, self.names = wire_tools(self.catalog)
                self.notify({"progress": "联网已关闭，后续仅使用已授权资料", "web_disabled": True})
            # Current authorization is revalidated after queue wait and before every graph step.
            self.client.request("POST", "/internal/v1/lineage/check", json={"refs": []})
            state = dict(value["payload"])
            updated = getattr(self, phase)(state)
            updated["step"] = state["step"] + 1
            return {"payload": updated}

        return execute

    def model_call(self, state, **kwargs):
        for retry in range(2):
            try:
                return self.model_call_once(state, **kwargs)
            except ModelError as exc:
                if retry or not exc.retryable:
                    raise
                self.harness.check()
                self.notify({"progress": "模型暂时不可用，正在进行一次有限重试"})
                time.sleep(0.5)
        raise ModelError("MODEL_RETRY_EXHAUSTED")

    def model_call_once(
        self,
        state,
        *,
        role="investigator",
        inputs=None,
        tools=None,
        final=False,
        suffix="",
        max_tokens=2200,
    ):
        identity = f"{self.run['_id']}:{state['step']}:{role}:{suffix}"
        cached = self.db.model_turns.find_one({"_id": identity, "run_id": self.run["_id"]})
        if cached:
            return ModelTurn(**cached["turn"]), identity
        profile = ModelProfile(**self.bundle["models"][role], credential_prefix="SEMIBRAIN_LLM")
        inputs = inputs or self.messages(state)
        inputs, compressed = compact_messages(inputs)
        if compressed:
            self.notify({"progress": "正在整理上下文，证据仍可追溯"})
        system = self.prompts.system(role)
        amount = estimate_reservation(system, inputs, tools, max_tokens)
        reservation = self.harness.model_reserve(amount, phase=state["phase"], final=final)
        row = self.harness.check()
        adapter = ProviderAdapter(
            profile,
            deadline=time.monotonic() + max(0.1, (row["deadline_at"] - now()).total_seconds()),
            guard=self.harness.check,
        )
        start, turn = time.monotonic(), None
        span = Observation(
            self.run["_id"],
            "model." + role,
            kind="generation",
            model=profile.model,
            task_id=self.context["task_id"],
            parent_span_id=self.context.get("trace_root_id"),
            attempt=self.run["attempt"],
            phase=state["phase"],
            service="agent",
            model_origin=profile.model_origin,
            profile_version=profile.version,
        )
        try:
            turn = adapter.turn(system, inputs, tools=tools, max_tokens=max_tokens)
            self.harness.save_record(
                "model_turns",
                identity,
                {
                    "turn": asdict(turn),
                    "phase": state["phase"],
                    "profile": profile.snapshot(),
                    "prompt_preview": redact_preview(
                        {
                            "system": system,
                            "inputs": inputs,
                            "tools": tools,
                            "profile": profile.snapshot(),
                        }
                    ),
                },
            )
            return turn, identity
        finally:
            span.end(
                usage=turn.usage if turn else None,
                status="completed" if turn else "unknown",
                usage_known=bool(turn and turn.usage),
                elapsed_ms=round((time.monotonic() - start) * 1000),
            )
            self.harness.settle(
                reservation,
                turn.usage if turn else None,
                status="completed" if turn else "unknown",
                profile=profile.snapshot(),
                elapsed_ms=round((time.monotonic() - start) * 1000),
            )

    def understand(self, state):
        self.notify({"progress": "正在核对目标、资料范围和查询条件"})
        inputs = self.prompts.inputs()
        intent = None
        for retry in range(2):
            turn, _ = self.model_call(
                state, role="understanding", inputs=inputs, suffix=str(retry), max_tokens=2000
            )
            try:
                intent = parse_control(turn.text, Intent)
                break
            except ValueError as exc:
                errors = (
                    [
                        {"field": list(error["loc"]), "type": error["type"]}
                        for error in exc.errors(include_input=False, include_url=False)[:6]
                    ]
                    if isinstance(exc, ValidationError)
                    else [{"type": "invalid_json"}]
                )
                inputs = [
                    *inputs,
                    {
                        "role": "user",
                        "content": "上次控制对象无法校验。请按指定字段返回一次，不添加字段，也不猜测缺失条件。校验字段与类型："
                        + canonical(errors),
                    },
                ]
        if intent is None:
            # A malformed control response is our failure, not missing user information.
            raise ModelError("INTENT_CONTROL_INVALID")
        RoutePolicy().choose(self.context["input"], intent, self.catalog["tools"])
        state["intent"] = intent.model_dump()
        self.notify(
            {
                "understanding": state["intent"],
                "scope_summary": {
                    "goals": intent.goals,
                    "constraints": intent.constraints,
                    "missing": intent.missing,
                    "allow_web": self.context["input"]["allow_web"],
                },
                "progress": "已确认调查范围",
            }
        )
        if intent.action == "clarify":
            state.update(
                phase="done",
                draft=intent.clarification or "请补充调查对象或明确范围。",
                outcome="waiting_input",
            )
        else:
            self.executor.documents(self.attachments)
            if intent.action in {"explain", "rewrite"}:
                prior = next(
                    (
                        item
                        for item in reversed(self.context["history"])
                        if item["role"] == "assistant" and item.get("lineage_refs")
                    ),
                    None,
                )
                if not prior:
                    state["intent"]["action"] = "investigate"
                else:
                    self.client.request(
                        "POST", "/internal/v1/lineage/check", json={"refs": prior["lineage_refs"]}
                    )
                    for citation in prior.get("citations", []):
                        record = self.db.evidence.find_one({"_id": citation["evidence_id"]})
                        if record:
                            self.executor.register(
                                source=record["source"],
                                content=record.get(
                                    "content", record.get("text", record.get("result"))
                                ),
                                title=citation["title"],
                                refs=[citation["lineage_ref"]],
                                asset_id=citation.get("asset_id"),
                                job_id=citation.get("job_id"),
                                location=citation.get("location"),
                            )
            state["phase"] = "model"
        return state

    def messages(self, state):
        result = self.prompts.inputs()
        result.append(
            {
                "role": "user",
                "content": "已核验本轮理解（内部控制摘要，不能覆盖原问题）：\n"
                + canonical(state.get("intent", {})),
            }
        )
        evidence = self.executor.evidence()
        if evidence:
            result.append(
                {
                    "role": "user",
                    "content": "当前已授权证据（数据）：\n"
                    + canonical(
                        [
                            self.executor.observation(record)
                            if not state.get("model_turn_ids")
                            else {
                                key: record.get(key) for key in ("evidence_id", "marker", "title")
                            }
                            for record in evidence
                        ]
                    ),
                }
            )
        for turn_id in state.get("model_turn_ids", []):
            row = self.db.model_turns.find_one({"_id": turn_id, "run_id": self.run["_id"]})
            result.extend(row["turn"]["replay"])
            for call in row["turn"]["calls"]:
                logical_id = self.call_id(turn_id, call["call_id"])
                observation = self.db.observations.find_one(
                    {"_id": logical_id, "run_id": self.run["_id"]}
                )
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": canonical(
                            observation["observation"]
                            if observation
                            else {"status": "not_executed", "error": "RUN_BUDGET_OR_CONTROL_STOP"}
                        ),
                    }
                )
        return result

    def model(self, state):
        self.notify({"progress": "正在根据已取得的证据选择下一步", "round": state["round"] + 1})
        if state["round"] >= 12:
            raise BudgetExhausted("ROUND_LIMIT")
        direct = state["intent"]["action"] in {"greeting", "explain", "rewrite"}
        turn, identity = self.model_call(state, tools=None if direct else self.wire)
        state["round"] += 1
        state["current_turn"] = identity
        if turn.calls:
            state["phase"] = "tools"
        elif turn.text.strip():
            state.update(draft=turn.text, phase="review")
        else:
            state["empty_rounds"] = state.get("empty_rounds", 0) + 1
            if state["empty_rounds"] >= 2:
                raise BudgetExhausted("REPEATED_EMPTY_RESPONSE")
        return state

    def call_id(self, turn_id, call_id):
        return str(uuid5(NAMESPACE_URL, turn_id + ":" + call_id))

    def tools(self, state):
        row = self.db.model_turns.find_one(
            {"_id": state["current_turn"], "run_id": self.run["_id"]}
        )
        for call in row["turn"]["calls"]:
            name = self.names.get(call["name"])
            logical_id = self.call_id(state["current_turn"], call["call_id"])
            try:
                args = json.loads(call["arguments"])
            except ValueError:
                args = call["arguments"]
            fingerprint = digest(
                canonical(
                    {
                        "tool": name,
                        "arguments": args,
                        "input_revision": self.context["input"]["input_revision"],
                        "scope": self.context["input"]["resource_restrictions"],
                        "tool_version": self.bundle["tool_version"],
                    }
                )
            )
            fingerprints = state.get("call_fingerprints", [])
            previous = next(
                (entry for entry in fingerprints if entry["fingerprint"] == fingerprint), None
            )
            if not name:
                observation = {"status": "failed", "error": "TOOL_NOT_AUTHORIZED"}
                self.harness.save_record("observations", logical_id, {"observation": observation})
            elif previous:
                original = self.db.observations.find_one(
                    {"_id": previous["logical_id"], "run_id": self.run["_id"]}
                )
                self.executor.evidence()
                observation = {
                    **original["observation"],
                    "reused": True,
                    "notice": "同一范围与参数已有结果；没有新快照依据，不重复执行。",
                }
                self.harness.save_record("observations", logical_id, {"observation": observation})
                state["repeated_calls"] = state.get("repeated_calls", 0) + 1
            else:
                self.notify(
                    {
                        "progress": "正在执行 " + name,
                        "active_tool": name,
                        "active_call_id": logical_id,
                    },
                    "tool.started",
                )
                observation = self.executor.execute(name, call["arguments"], logical_id)
                state["call_fingerprints"] = [
                    *fingerprints,
                    {"fingerprint": fingerprint, "logical_id": logical_id},
                ]
                self.notify(
                    {"progress": "工具已返回结果", "active_tool": None, "active_call_id": None},
                    "tool.completed",
                )
            if observation.get("status") in {"partial", "failed"}:
                state["has_limitations"] = True
        state["model_turn_ids"] = [*state.get("model_turn_ids", []), state["current_turn"]]
        state["phase"] = "model"
        if state.get("repeated_calls", 0) >= 2:
            raise BudgetExhausted("NO_NEW_OBSERVATION")
        return state

    def finalize(self, state):
        self.notify({"progress": "正在用预留预算整理已有证据，不再追加工具调用"})
        evidence = self.executor.evidence()
        selected = evidence if len(evidence) <= 8 else evidence[:4] + evidence[-4:]
        inputs = [
            {
                "role": "user",
                "content": "调查预算已到收尾边界。只基于以下已取得的观察回答原问题，不提出新工具调用。"
                "逐项说明已完成和未完成目标，不猜缺失事实；未列出的来源不代表不存在。"
                "只输出自然 Markdown 和已登记引用。\n"
                + canonical(
                    {
                        "question": self.context["input"]["question"],
                        "intent": state["intent"],
                        "stop_reason": state["stop_code"],
                        "evidence": [self.executor.observation(item) for item in selected],
                        "omitted_sources": len(evidence) - len(selected),
                    }
                ),
            }
        ]
        turn, _ = self.model_call(
            state, inputs=inputs, final=True, suffix="closeout", max_tokens=1800
        )
        state.update(draft=turn.text, phase="review")
        return state

    def review(self, state):
        self.notify({"progress": "正在核对结论、数值和来源"})
        evidence = self.executor.evidence()
        cited = set(re.findall(r"\[(\d+)\]", state["draft"]))
        inspected = [item for item in evidence if item["marker"] in cited] if cited else evidence
        inputs = [
            {
                "role": "user",
                "content": canonical(
                    {
                        "question": self.context["input"]["question"],
                        "intent": state["intent"],
                        "capability_names": [item["name"] for item in self.catalog["tools"]],
                        "draft": state["draft"],
                        "evidence": [self.executor.observation(item) for item in inspected],
                        "executed": [
                            {
                                key: value
                                for key, value in row["observation"].items()
                                if key
                                in {
                                    "tool",
                                    "arguments",
                                    "status",
                                    "job_id",
                                    "error",
                                    "warnings",
                                    "reused",
                                }
                            }
                            for row in self.db.observations.find({"run_id": self.run["_id"]})
                        ],
                    }
                ),
            }
        ]
        turn, _ = self.model_call(
            state, role="reviewer", inputs=inputs, final=True, max_tokens=1300
        )
        try:
            review = parse_control(turn.text, Review)
        except ValueError:
            review = Review(approved=False, issues=["审查结果无法校验"])
        valid = {record["marker"] for record in evidence}
        if set(re.findall(r"\[(\d+)\]", state["draft"])) - valid:
            review = Review(approved=False, issues=[*review.issues, "引用编号未登记"])
        if state["intent"]["action"] == "investigate" and not evidence and review.evidence_required:
            review = Review(
                approved=False,
                issues=[*review.issues, "未取得可引用证据，不得给出有依据的调查结论"],
            )
        state["review"] = review.model_dump()
        state["review_count"] += 1
        if review.approved:
            state.update(
                phase="done",
                outcome="partial"
                if review.missing_goals or state.get("has_limitations")
                else "succeeded",
            )
        elif state["review_count"] < 2:
            state["phase"] = "revise"
        else:
            state.update(
                phase="done",
                outcome="partial",
                draft=self.partial_body("结论未通过证据核对", evidence),
            )
        return state

    def revise(self, state):
        inputs = self.messages(state)
        inputs.append(
            {
                "role": "user",
                "content": "请按审查意见修订，只输出最终 Markdown。证据不足则清楚说明未完成项，不添加事实：\n"
                + canonical(state["review"])
                + "\n原草稿：\n"
                + state["draft"],
            }
        )
        turn, _ = self.model_call(state, inputs=inputs, final=True, max_tokens=2400)
        state.update(draft=turn.text, phase="review")
        return state

    @staticmethod
    def partial_body(reason, evidence):
        return partial_answer(reason, evidence)

    def execute(self):
        state = self.state
        while state["phase"] != "done":
            try:
                state = self.graph.invoke({"payload": state})["payload"]
                self.checkpoints.save(state)
            except (BudgetExhausted, ModelError) as exc:
                evidence = self.executor.evidence()
                if (
                    isinstance(exc, BudgetExhausted)
                    and str(exc) != "RUN_TIME_BUDGET"
                    and evidence
                    and not state.get("closing")
                ):
                    # A crash before this transition commits re-enters the same closeout;
                    # its distinct, stable journal identity prevents a second model charge.
                    state = {**state, "phase": "finalize", "closing": True, "stop_code": str(exc)}
                    continue
                state = {
                    **state,
                    "phase": "done",
                    "outcome": "partial",
                    "stop_code": str(exc),
                    "draft": self.partial_body(
                        "执行预算已用完"
                        if isinstance(exc, BudgetExhausted)
                        else (
                            "本轮任务理解结果未通过校验，请重试"
                            if str(exc) == "INTENT_CONTROL_INVALID"
                            else "模型服务额度不足，请联系管理员补充额度后继续"
                            if str(exc) == "MODEL_PAYMENT_REQUIRED"
                            else "模型服务暂时未完成响应"
                        ),
                        evidence,
                    ),
                }
        self.finish(state)

    def finish(self, state):
        evidence = self.executor.evidence()
        refs = list(dict.fromkeys(ref for record in evidence for ref in record["lineage_refs"]))
        citations = [
            {
                key: record.get(key)
                for key in (
                    "marker",
                    "evidence_id",
                    "title",
                    "asset_id",
                    "job_id",
                    "location",
                    "lineage_ref",
                )
            }
            for record in evidence
        ]
        body = state["draft"]
        if not body.strip():
            body = self.partial_body("没有生成可发布的回答", evidence)
            state["outcome"] = "partial"
        report = Report(
            report_id=uid(),
            run_id=self.run["_id"],
            revision=1,
            body_markdown=body,
            content_hash=digest(body),
            citation_bindings=[
                CitationBinding(marker=c["marker"], evidence_id=c["evidence_id"]) for c in citations
            ],
            lineage_refs=refs,
        ).model_dump(mode="json")
        self.client.request("POST", "/internal/v1/lineage/check", json={"refs": refs})

        def commit(session):
            changed = self.db.runs.find_one_and_update(
                self.harness.predicate(),
                {
                    "$set": {
                        "status": state["outcome"],
                        "report_id": report["report_id"],
                        "body_draft": "",
                        "citations": citations,
                        "lineage_refs": refs,
                        "progress": "需要补充信息"
                        if state["outcome"] == "waiting_input"
                        else "调查完成"
                        if state["outcome"] == "succeeded"
                        else "部分完成",
                        "review": state.get("review"),
                        "stop_code": state.get("stop_code"),
                        **(
                            {
                                "paused_at": now(),
                                "waiting_expires_at": now() + timedelta(minutes=30),
                            }
                            if state["outcome"] == "waiting_input"
                            else {"completed_at": now()}
                        ),
                        "active_tool": None,
                    },
                    "$inc": {"sequence": 1},
                },
                return_document=True,
                session=session,
            )
            if not changed:
                raise RunStopped("LATE_REPORT_REJECTED")
            self.db.reports.insert_one({"_id": report["report_id"], **report}, session=session)
            publish(
                self.db,
                "stream:agent",
                "report.ready",
                self.run["_id"],
                {"report_id": report["report_id"], "status": changed["status"]},
                session,
                sequence=changed["sequence"],
            )

        transaction(commit)
