"""Recognize equivalent numeric reference typography without rewriting answer prose."""

import re

_REFERENCE = re.compile(r"\[\s*([0-9０-９]+)\s*\]|［\s*([0-9０-９]+)\s*］|【\s*([0-9０-９]+)\s*】")
_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def cited_markers(markdown: str) -> set[str]:
    # Recognition does not authorize an id: callers still check every marker
    # against the server evidence registry and review the associated claims.
    return {next(part for part in match.groups() if part is not None).translate(_DIGITS)
            for match in _REFERENCE.finditer(markdown)}


def retain_markers(markdown: str, markers: set[str]) -> str:
    """Remove surplus numeric references after explicit source-bound review.

    Never create a reference or change factual text, URLs, or Markdown link labels.
    """
    def keep(match):
        value = next(part for part in match.groups() if part is not None).translate(_DIGITS)
        if markdown[match.end():].startswith(("(", "[", ":")):
            return match[0]
        return match[0] if value in markers else ""
    return _REFERENCE.sub(keep, markdown)
