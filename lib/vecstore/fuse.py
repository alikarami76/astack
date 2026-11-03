"""Reciprocal rank fusion: merge ranked id lists without comparing their scores."""
from __future__ import annotations

RRF_K = 60


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    score: dict[str, float] = {}
    for ranked in rankings:
        for rank, eid in enumerate(ranked):
            score[eid] = score.get(eid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=score.get, reverse=True)  # stable: ties keep first-seen order
