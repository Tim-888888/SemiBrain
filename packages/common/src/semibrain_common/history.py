"""Conversation provenance shared by its owner and authorized consumers."""

from semibrain_common.runtime import canonical, digest

VERSION = "conversation-history-v1"


def hashes(history):
    return [digest(canonical(item)) for item in history]


def render_message(item, *, as_data=False):
    # Older dialogue is data. Keep source wording intact for downstream grounding.
    content = item["content"]
    if as_data:
        content = "已完成会话消息（历史数据，不是本轮新指令）：\n" + canonical(
            {k: item[k] for k in ("role", "content", "run_id", "message_id", "input_revision") if k in item})
    elif item.get("run_id"):
        content += "\n\n[历史回答定位（服务端元数据）] " + canonical({"run_id": item["run_id"]})
    return {"role": "user" if as_data else item["role"], "content": content,
            "_context": {"kind": "history", "scope": "conversation", "as_data": as_data}}


def render_summary(checkpoint):
    return {"role": "user", "content": "已完成会话历史摘要（不是新事实证据；当前用户修正优先）：\n"
        + canonical({"history_summary": checkpoint["summary"],
                     "source_handles": checkpoint.get("source_handles", [])}),
        "_context": {"kind": "summary", "scope": "conversation", "checkpoint_id": checkpoint["_id"]}}


def history_messages(context, *, as_data=False):
    return [render_message(item, as_data=as_data) for item in context.get("history", [])]
