"""Server-owned roles, scopes and validated dependency plans."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MULTI_LIMITS = {
    "rounds": 40,
    "tools": 40,
    "tokens": 200000,
    "seconds": 300,
    "searches": 3,
    "pages": 5,
    "final_token_reserve": 20000,
    "final_seconds_reserve": 45,
}
ROLE_TOOLS = {
    "sqlbot": frozenset(
        {
            "business.search_lots",
            "business.get_lot_context",
            "business.get_yield_summary",
            "business.get_process_history",
            "business.get_fdc_alerts",
            "business.query",
            "business.statistics",
            "evidence.read",
        }
    ),
    "rag": frozenset({"knowledge.search", "knowledge.read", "evidence.read"}),
    "vision": frozenset({"vision.inspect", "evidence.read"}),
    "tool": frozenset(
        {
            "web.search",
            "web.fetch",
            "web.read",
            "sandbox.python",
            "sandbox.files",
            "evidence.read",
            "business.statistics",
        }
    ),
}


class Deliverables(BaseModel):
    """Internal completion requirements; never a schema for the user's answer."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["evidence", "dataset", "python"] = "evidence"
    artifact_formats: list[Literal["png", "csv", "json", "md", "txt"]] = Field(
        default_factory=list, max_length=5
    )
    lot_limit: int | None = Field(
        default=None, ge=1, le=50,
        description="仅当用户要求按批次编号升序读取前N条批次时填写N；不可自行添加。",
    )


class PlannedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    role: Literal["sqlbot", "rag", "vision", "tool"]
    goal_indices: list[int] = Field(min_length=1, max_length=12)
    depends_on: list[str] = Field(default_factory=list, max_length=4)
    deliverables: Deliverables = Field(default_factory=Deliverables)
    reuse_key: str | None = Field(
        default=None, description="补查计划可引用上一版已验证完成且要求相同的任务key，复用其产物。"
    )
    gap: str = Field(default="", max_length=600, description="补查对应的原目标缺口；不新增目标。")
    target_refs: list[str] = Field(default_factory=list, max_length=4,
        description="同一路径补查必须列已发现但尚未读取的document_id或URL；不填同义查询。")


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[PlannedTask] = Field(max_length=4)
    finish_with_existing: bool = Field(default=False,
        description="仅补查阶段使用：无可用新路径时填true，tasks为空，原目标全部列入synthesis_goal_indices，据已有证据部分收尾。")
    synthesis_goal_indices: list[int] = Field(
        default_factory=list, max_length=12,
        description="仅格式、范围声明等不需独立取证的目标，由最终汇总完成。",
    )


def validate_plan(plan, goals, available_roles):
    if bool(plan.tasks) == plan.finish_with_existing:
        raise ValueError("EMPTY_PLAN_REQUIRES_EXPLICIT_FINISH")
    keys = [task.key for task in plan.tasks]
    if len(set(keys)) != len(keys) or len({t.role for t in plan.tasks}) != len(keys):
        raise ValueError("DUPLICATE_TASK_OR_ROLE")
    for task in plan.tasks:
        if task.role not in available_roles:
            raise ValueError("ROLE_UNAVAILABLE")
        if any(i < 0 or i >= len(goals) for i in task.goal_indices):
            raise ValueError("GOAL_OUTSIDE_ORIGINAL_SCOPE")
        requirement = task.deliverables
        if requirement.kind == "dataset" and task.role != "sqlbot":
            raise ValueError("DATASET_REQUIRES_SQLBOT")
        if (requirement.kind == "python" or requirement.artifact_formats) and task.role != "tool":
            raise ValueError("COMPUTATION_REQUIRES_TOOL")
        if requirement.artifact_formats and requirement.kind != "python":
            raise ValueError("EXPORT_REQUIRES_PYTHON")
        if requirement.lot_limit is not None and requirement.kind != "dataset":
            raise ValueError("LOT_LIMIT_REQUIRES_DATASET")
        if len(set(task.depends_on)) != len(task.depends_on) or any(
            key not in keys or key == task.key for key in task.depends_on
        ):
            raise ValueError("INVALID_DEPENDENCY")
    if any(i < 0 or i >= len(goals) for i in plan.synthesis_goal_indices):
        raise ValueError("GOAL_OUTSIDE_ORIGINAL_SCOPE")
    covered = set(plan.synthesis_goal_indices).union(*(set(t.goal_indices) for t in plan.tasks))
    if covered != set(range(len(goals))):
        raise ValueError("UNASSIGNED_ORIGINAL_GOAL")
    by_key = {task.key: task for task in plan.tasks}
    for task in plan.tasks:
        if task.deliverables.kind == "python":
            for key in task.depends_on:
                producer = by_key[key]
                if producer.role == "sqlbot" and producer.deliverables.kind != "dataset":
                    raise ValueError("PYTHON_QUERY_DEPENDENCY_REQUIRES_DATASET")
    completed = set()
    while len(completed) < len(keys):
        ready = [
            t.key for t in plan.tasks if t.key not in completed and set(t.depends_on) <= completed
        ]
        if not ready:
            raise ValueError("CYCLIC_PLAN")
        completed.update(ready)
    return plan


def ready_tasks(tasks):
    finished = {t["key"] for t in tasks if t["status"] in {"succeeded", "partial", "failed"}}
    return [
        t
        for t in tasks
        if t["status"] in {"queued", "running"} and set(t["depends_on"]) <= finished
    ][:3]
