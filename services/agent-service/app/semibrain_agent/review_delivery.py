"""Retain exact reviewed content; never ask a fallback model to invent a new answer."""

import re

from semibrain_agent.citations import cited_markers


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
    state.pop("reviewed_content", None)
    if verdict.approved and not verdict.issues:
        state["reviewed_content"] = state["draft"]
        return
    valid = {e["marker"] for e in evidence}
    supported = set(verdict.supported_blocks)
    keep = []
    for block in draft_blocks(state["draft"]):
        markers = cited_markers(block["text"])
        if block["id"] in supported and markers and markers <= valid:
            keep.append(block["text"])
    if keep:
        state["reviewed_content"] = "\n\n".join(keep)


def reviewed_partial(state, reason):
    content = state.get("reviewed_content")
    if not content:
        return None
    return content + "\n\n> 本次仅交付上述已核对内容；" + reason + "，其余内容暂未完成。"
