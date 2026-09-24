"""Retain exact reviewed content; never ask a fallback model to invent a new answer."""

import re

from semibrain_agent.citations import cited_markers
from semibrain_agent.partial import yield_observation


def draft_blocks(draft):
    # Keep fenced code intact, including blank lines inside it.
    blocks, current, fence = [], [], None
    for line in draft.splitlines():
        token = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if token:
            value = token[1]
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        if not line.strip() and not fence:
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [{"id": i, "text": text} for i, text in enumerate(blocks)]


def retain_reviewed(state, verdict, evidence):
    previous = set(state.pop("reviewed_blocks", []))
    state.pop("reviewed_content", None)
    state["verified_observations"] = [value for record in evidence
                                      if (value := yield_observation(record))][:8]
    if verdict.approved and not verdict.issues:
        state["reviewed_content"] = state["draft"]
        state["reviewed_blocks"] = [b["text"] for b in draft_blocks(state["draft"])
                                    if cited_markers(b["text"])]
        return
    valid = {e["marker"] for e in evidence}
    if verdict.issues and not verdict.issue_blocks:
        return  # Unlocated defects cannot justify retaining model-selected fragments.
    supported, rejected = set(verdict.supported_blocks), set(verdict.issue_blocks)
    keep = []
    for block in draft_blocks(state["draft"]):
        markers = cited_markers(block["text"])
        if (block["id"] not in rejected and markers and markers <= valid
                and (block["id"] in supported or block["text"] in previous)):
            keep.append(block["text"])
    if keep:
        state["reviewed_blocks"] = keep
        state["reviewed_content"] = "\n\n".join(keep)


def reviewed_partial(state, reason):
    content = state.get("reviewed_content")
    observations = state.get("verified_observations", [])
    if not content and not observations:
        return None
    parts = (["已取得的查询结果：\n\n" + "\n".join(observations)] if observations else [])
    if content:
        parts.append(content)
    return "\n\n".join(parts) + "\n\n> 本次仅交付上述已核对内容；" + reason + "，其余内容暂未完成。"
