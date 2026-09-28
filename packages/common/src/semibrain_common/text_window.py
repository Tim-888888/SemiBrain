"""Verbatim ranges, with optional local search over retained originals."""

import re


def read_window(text, offset=0, length=7000, query=None):
    match = None
    if query:
        match = re.search(re.escape(query), text[offset:], re.I)
        if match:
            offset = max(offset, offset + match.start() - min(200, length // 4))
    end = min(len(text), offset + length)
    return {"text": text[offset:end], "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_characters": len(text), "query_found": bool(match) if query else None,
            "partial": offset > 0 or end < len(text)}
