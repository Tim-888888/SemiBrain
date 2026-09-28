"""Bounded lexical MMR, following WeKnora's relevance-minus-redundancy selection.

Chinese character bigrams supplement word tokens; no model request or reindexing.
Exact values remain in the text so differing numeric evidence is not hard-deduped.
"""

import math
import re


def tokens(text):
    terms = set(re.findall(r"[a-z0-9]+(?:[._%+-][a-z0-9]+)*", text.lower()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return terms


def similarity(a, b):
    return len(a & b) / len(a | b) if a or b else 0.0


def select_mmr(candidates, top_k, weight=0.75):
    unique, bodies = [], set()
    for row in candidates:
        # Preserve origin distinctions: synthetic and public facts are not interchangeable.
        key = (row.get("data_origin"), re.sub(r"\s+", " ", row["text"]).strip())
        if key not in bodies:
            unique.append(row)
            bodies.add(key)
    remaining = list(enumerate(unique))
    terms = [tokens(r["text"]) for r in unique]
    relevance = []
    for i, row in enumerate(unique):
        value = row.get("rerank_score")
        relevance.append(max(0.0, min(1.0, float(value))) if isinstance(value, (int, float)) and math.isfinite(value)
                         else 1.0 / (1.0 + i / 4.0))
    selected, redundancy = [], [0.0] * len(unique)
    while remaining and len(selected) < top_k:
        index, row = max(remaining, key=lambda pair: (
            weight * relevance[pair[0]] - (1 - weight) * redundancy[pair[0]], -pair[0]))
        selected.append(row)
        remaining = [(i, r) for i, r in remaining if i != index]
        for i, _ in remaining:
            redundancy[i] = max(redundancy[i], similarity(terms[i], terms[index]))
    return selected, {"method": "lexical_mmr", "weight": weight, "candidates": len(candidates),
                      "exact_duplicates_removed": len(candidates) - len(unique), "selected": len(selected)}
