"""Aggregates per-country Israel-sympathy scores from classified resolutions.

Pure logic, no I/O -- see docstring on `score_countries` for the formula.
"""
from __future__ import annotations

import dataclasses

from src.models import Classification, Resolution


@dataclasses.dataclass
class CountryScore:
    country: str
    raw_score: int
    resolutions_counted: int  # resolutions this country cast a Y/N vote on
    normalized_score: float  # 0..100, 50 = neutral


def score_countries(
    resolutions: list[Resolution],
    classifications: dict[str, Classification],
) -> list[CountryScore]:
    """Compute each country's Israel-sympathy score.

    For every analyzed resolution (one with a classification), each country's
    vote sign (Y=+1, N=-1, Abstain/non-voting=0) is multiplied by the
    resolution's direction weight (sympathetic=+1, neutral=0,
    unsympathetic=-1) and summed. This means "yes" on a pro-Israel resolution
    and "no" on an anti-Israel resolution both score +1 -- the vote's meaning
    flips with what the resolution actually says.

    Normalization uses the fixed theoretical range [-N, N] (N = number of
    resolutions analyzed) rather than min-max across countries, so 50 always
    means "perfectly neutral / non-participating" regardless of which
    countries happen to be most extreme.
    """
    analyzed = [r for r in resolutions if r.symbol in classifications]
    n = len(analyzed)

    raw_scores: dict[str, int] = {}
    participation: dict[str, int] = {}

    for resolution in analyzed:
        direction_weight = classifications[resolution.symbol].direction.weight
        for country_vote in resolution.votes:
            country = country_vote.country
            sign = country_vote.vote.sign
            raw_scores[country] = raw_scores.get(country, 0) + direction_weight * sign
            if sign != 0:
                participation[country] = participation.get(country, 0) + 1

    results = []
    for country, raw_score in raw_scores.items():
        normalized = 50.0 if n == 0 else (raw_score + n) / (2 * n) * 100
        results.append(
            CountryScore(
                country=country,
                raw_score=raw_score,
                resolutions_counted=participation.get(country, 0),
                normalized_score=round(normalized, 2),
            )
        )

    results.sort(key=lambda c: c.normalized_score, reverse=True)
    return results
