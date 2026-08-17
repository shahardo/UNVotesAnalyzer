"""Core data structures shared across fetch / analysis stages."""
from __future__ import annotations

import dataclasses
from enum import Enum


class Vote(str, Enum):
    YES = "Y"
    NO = "N"
    ABSTAIN = "A"  # includes non-voting: UNDL data doesn't distinguish them

    @property
    def sign(self) -> int:
        return {"Y": 1, "N": -1, "A": 0}[self.value]


class Direction(str, Enum):
    SYMPATHETIC = "sympathetic"
    NEUTRAL = "neutral"
    UNSYMPATHETIC = "unsympathetic"

    @property
    def weight(self) -> int:
        return {"sympathetic": 1, "neutral": 0, "unsympathetic": -1}[self.value]


@dataclasses.dataclass
class CountryVote:
    country: str
    vote: Vote


@dataclasses.dataclass
class Resolution:
    """A single UN General Assembly resolution with a recorded vote."""

    symbol: str  # e.g. "A/RES/ES-10/21"
    title: str
    subjects: list[str]
    date: str | None
    summary: str
    votes: list[CountryVote]


@dataclasses.dataclass
class Classification:
    resolution_symbol: str
    direction: Direction
    confidence: float
    reasoning: str


def vote_tally(votes: list[CountryVote]) -> dict[str, int]:
    tally = {"Y": 0, "N": 0, "A": 0}
    for v in votes:
        tally[v.vote.value] += 1
    return tally
