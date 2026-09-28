"""Review Markdown at row/item granularity and reconstruct only accepted units."""

import re

from semibrain_agent.citations import cited_markers
from semibrain_agent.review_delivery import draft_blocks


def table_cells(line):
    # Escaped pipes and inline code are data, not column separators.
    cells, current, fence = [], [], ""
    for token in re.findall(r"\\.|`+|[^\\`|]+|\||\\", line.strip()):
        if token.startswith("`"):
            fence = "" if token == fence else token if not fence else fence
        if token == "|" and not fence:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(token)
    cells.append("".join(current).strip())
    if line.strip().startswith("|"):
        cells = cells[1:]
    if line.strip().endswith("|") and not line.rstrip().endswith("\\|"):
        cells = cells[:-1]
    return cells


def review_units(draft):
    raw = [b["text"] for b in draft_blocks(draft)]
    units = []
    for group, text in enumerate(raw):
        lines = text.splitlines()
        # A fenced block remains indivisible even if it contains Markdown syntax.
        fenced = bool(re.match(r"^\s{0,3}(`{3,}|~{3,})", text))
        table = (not fenced and len(lines) >= 3 and "|" in lines[0]
                 and all(re.fullmatch(r":?-{3,}:?", c) for c in table_cells(lines[1]))
                 and len(table_cells(lines[0])) == len(table_cells(lines[1])))
        adjacent = []
        if not fenced:
            for index in (group - 1, group + 1):
                if (0 <= index < len(raw) and cited_markers(raw[index])
                        and not re.search(r"(?m)^\s*(?:[#|`~]|[-*+]\s|\d+[.)]\s)", raw[index])):
                    adjacent.append(raw[index])
        shared = sorted(cited_markers("\n".join(adjacent)))
        if table:
            header = "\n".join(lines[:2])
            for line in lines[2:]:
                units.append({"text": header + "\n" + line, "kind": "table_row",
                              "group": group, "header": header, "row": line,
                              "adjacent_citations": shared})
        elif not fenced and re.match(r"^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)", text):
            # Split top-level items only; nested bullets/continuations stay with their parent.
            indent = re.match(r"^ *", text)[0]
            items = re.split(r"\n(?=" + indent + r"(?:[-*+]\s+|\d+[.)]\s+))", text)
            units.extend({"text": item, "kind": "list_item", "group": group,
                          "adjacent_citations": shared} for item in items)
        else:
            units.append({"text": text, "kind": "prose", "group": group})
    return [{"id": i, **unit} for i, unit in enumerate(units)]


def render_units(units, accepted, shared_citations=None):
    groups = []
    for unit in units:
        if unit["id"] not in accepted:
            continue
        if unit["kind"] == "table_row":
            row = unit["row"]
            inherited = (shared_citations or {}).get(unit["id"], [])
            if inherited:
                refs = " " + "".join("[" + marker + "]" for marker in inherited)
                row = row.rstrip()
                row = row[:-1].rstrip() + refs + " |" if row.endswith("|") else row + refs
            if groups and groups[-1][0] == unit["group"]:
                groups[-1][1] += "\n" + row
            else:
                groups.append([unit["group"], unit["header"] + "\n" + row])
        elif unit["kind"] == "list_item" and groups and groups[-1][0] == unit["group"]:
            refs = "".join("[" + m + "]" for m in (shared_citations or {}).get(unit["id"], []))
            groups[-1][1] += "\n" + unit["text"] + (" " + refs if refs else "")
        else:
            refs = "".join("[" + m + "]" for m in (shared_citations or {}).get(unit["id"], []))
            groups.append([unit["group"], unit["text"] + (" " + refs if refs else "")])
    # An accepted context heading must have surviving content beneath it.
    result, pending = [], []
    for _, text in groups:
        heading = re.fullmatch(r"(#{1,6})[ \t]+[^\n]+", text.strip())
        if heading:
            level = len(heading[1])
            pending = [(depth, title) for depth, title in pending if depth < level]
            pending.append((level, text))
        else:
            result.extend(title for _, title in pending)
            pending = []
            result.append(text)
    return "\n\n".join(result)
