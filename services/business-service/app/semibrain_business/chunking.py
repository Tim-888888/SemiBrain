"""Heading-aware, validated Markdown slices inspired by pinned WeKnora splitters.

Offsets refer to the canonical parsed text, not to a heading prepended for retrieval.
"""

import re

from markdown_it import MarkdownIt

from semibrain_business.document_images import image_spans

CHUNKER_VERSION = 'weknora-adapted-markdown-v1'
TARGET = 2200
HARD = 4300


def _split(text, *, target_size=TARGET, header_limit=900):
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    tokens = MarkdownIt('commonmark').enable('table').parse(text)
    protected = [(s['start'], s['end']) for s in image_spans(text)]
    headings, boundaries = [], {0, len(text)}
    stack = []
    for i, token in enumerate(tokens):
        if not token.map or token.level != 0:
            continue
        start, end = offsets[token.map[0]], offsets[token.map[1]]
        boundaries.update((start, end))
        if token.type == 'heading_open':
            level = int(token.tag[1:])
            stack = [(n, t) for n, t in stack if n < level]
            stack.append((level, tokens[i+1].content[:300]))
            headings.append((start, ' > '.join(t for _, t in stack)))
        if token.type in {'fence', 'code_block', 'table_open'} and end-start <= HARD:
            protected.append((start, end))
    protected += [(m.start(), m.end()) for m in re.finditer(r'\$\$[\s\S]*?\$\$|`[^`\n]+`|\[[^\]\n]{1,600}\]\([^\n]{1,1000}?\)', text)
                  if m.end()-m.start() <= HARD]
    boundaries.update(m.end() for m in re.finditer(r'\n\s*\n|[。！？]\s*|\n', text))
    boundaries = sorted(b for b in boundaries if not any(a < b < e for a, e in protected))
    rows, start = [], 0
    while start < len(text):
        target = min(start+target_size, len(text))
        # Prefer a nearby structural boundary. Small adjoining blocks remain together.
        candidates = [b for b in boundaries if start+target_size//2 <= b <= target]
        end = max(candidates) if candidates else target
        for a, e in protected:
            if a < end < e:
                end = a if a > start else e
        if len(text)-end < 250 and len(text)-start <= HARD:
            end = len(text)
        if end <= start or end-start > HARD:
            # An enormous malformed construct must not swallow the remaining document.
            end = min(start+target_size, len(text))
        header = next((h for p, h in reversed(headings) if p <= start), '')[:header_limit]
        rows.append({'text': text[start:end], 'context_header': header,
                     'location': {'representation': 'parsed_markdown', 'character_start': start,
                                  'character_end': end, 'line_start': text.count('\n', 0, start)+1,
                                  'line_end': text.count('\n', 0, end)+1}})
        start = end
    # Validate exact coverage and the vector field's UTF-8 limit, then use a bounded fallback.
    if ''.join(r['text'] for r in rows) != text or any(len((r['context_header']+'\n'+r['text']).encode()) > 15500 for r in rows):
        raise ValueError('MARKDOWN_CHUNK_VALIDATION_FAILED')
    return [r for r in rows if r['text'].strip()]


def split_markdown(text):
    try:
        rows = _split(text)
    except ValueError:
        # Retry with a smaller text/context projection; source text and image bounds
        # remain authoritative. A second failure is explicit, never silently indexed.
        rows = _split(text, target_size=1400, header_limit=300)
        for row in rows:
            row['fallback'] = 'bounded_structural'
    return rows
