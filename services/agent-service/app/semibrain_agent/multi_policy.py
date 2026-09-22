"""Server-owned roles, scopes and validated dependency plans."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MULTI_LIMITS = {
    "rounds": 40,
    "tools": 40,
    "tokens": 100000,
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


class PlannedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    role: Literal["sqlbot", "rag", "vision", "tool"]
    goal_indices: list[int] = Field(min_length=1, max_length=12)
    depends_on: list[str] = Field(default_factory=list, max_length=4)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[PlannedTask] = Field(min_length=1, max_length=4)


def validate_plan(plan, goals, available_roles):
    keys = [task.key for task in plan.tasks]
    if len(set(keys)) != len(keys) or len({t.role for t in plan.tasks}) != len(keys):
        raise ValueError("DUPLICATE_TASK_OR_ROLE")
    for task in plan.tasks:
        if task.role not in available_roles:
            raise ValueError("ROLE_UNAVAILABLE")
        if any(i < 0 or i >= len(goals) for i in task.goal_indices):
            raise ValueError("GOAL_OUTSIDE_ORIGINAL_SCOPE")
        if len(set(task.depends_on)) != len(task.depends_on) or any(
            key not in keys or key == task.key for key in task.depends_on
        ):
            raise ValueError("INVALID_DEPENDENCY")
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
