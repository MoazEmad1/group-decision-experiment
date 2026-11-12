"""Pure experiment rules: phase transitions, vote tallying, tie-breaking.

No IO, no asyncio, no DB. The session manager calls into this module; tests hit it directly.
Participants are referred to by an opaque string id (participant id or label).
"""

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.config import TimingConfig

ROUNDS = 2


# --- phases ------------------------------------------------------------------


class Phase(str, Enum):
    WAITING = "WAITING"
    ROUND_1_CHAT = "ROUND_1_CHAT"
    ROUND_1_VOTE = "ROUND_1_VOTE"
    ROUND_2_CHAT = "ROUND_2_CHAT"
    ROUND_2_VOTE = "ROUND_2_VOTE"
    RESULTS = "RESULTS"


NEXT_PHASE: dict[Phase, Phase] = {
    Phase.WAITING: Phase.ROUND_1_CHAT,
    Phase.ROUND_1_CHAT: Phase.ROUND_1_VOTE,
    Phase.ROUND_1_VOTE: Phase.ROUND_2_CHAT,
    Phase.ROUND_2_CHAT: Phase.ROUND_2_VOTE,
    Phase.ROUND_2_VOTE: Phase.RESULTS,
}

_ROUND_OF: dict[Phase, int] = {
    Phase.ROUND_1_CHAT: 1,
    Phase.ROUND_1_VOTE: 1,
    Phase.ROUND_2_CHAT: 2,
    Phase.ROUND_2_VOTE: 2,
}


def next_phase(phase: Phase) -> Phase:
    if phase not in NEXT_PHASE:
        raise ValueError(f"{phase.value} is terminal")
    return NEXT_PHASE[phase]


def round_of(phase: Phase) -> int | None:
    return _ROUND_OF.get(phase)


def is_chat(phase: Phase) -> bool:
    return phase in (Phase.ROUND_1_CHAT, Phase.ROUND_2_CHAT)


def is_vote(phase: Phase) -> bool:
    return phase in (Phase.ROUND_1_VOTE, Phase.ROUND_2_VOTE)


def phase_duration(phase: Phase, timing: TimingConfig) -> float | None:
    """Seconds until the timer advances this phase. None = no timer
    (WAITING advances when slots fill, RESULTS is terminal)."""
    if is_chat(phase):
        return timing.chat_seconds
    if is_vote(phase):
        return timing.vote_seconds
    return None


# --- votes -------------------------------------------------------------------


@dataclass(frozen=True)
class Vote:
    round: int
    voter: str
    target: str
    cast_at: datetime


@dataclass(frozen=True)
class RoundTally:
    round: int
    counts: dict[str, int]  # every participant present, in participant order, 0 if no votes
    abstentions: int


class TieBreak(str, Enum):
    ROUND_2 = "round2"  # round-2 votes alone decide
    COMBINED = "combined"  # round 1 + round 2, among the round-2 leaders
    RANDOM = "random"  # seeded draw among the remaining tied candidates


@dataclass(frozen=True)
class Decision:
    winner: str
    method: TieBreak
    tallies: dict[int, RoundTally]


def latest_votes(votes: Iterable[Vote], round: int, participants: Sequence[str]) -> dict[str, str]:
    """voter -> target, keeping each voter's latest vote in the round.

    Votes are append-only, so a voter may have several rows per round. Latest = greatest
    cast_at; equal timestamps keep input order (sort is stable), so pass votes in insert order.
    """
    members = set(participants)
    latest: dict[str, str] = {}
    for vote in sorted((v for v in votes if v.round == round), key=lambda v: v.cast_at):
        if vote.voter not in members or vote.target not in members:
            raise ValueError(f"vote references a non-participant: {vote}")
        latest[vote.voter] = vote.target
    return latest


def tally_round(votes: Iterable[Vote], round: int, participants: Sequence[str]) -> RoundTally:
    latest = latest_votes(votes, round, participants)
    counts = {p: 0 for p in participants}
    for target in latest.values():
        counts[target] += 1
    return RoundTally(round=round, counts=counts, abstentions=len(participants) - len(latest))


def everyone_voted(votes: Iterable[Vote], round: int, participants: Sequence[str]) -> bool:
    """True once every participant has at least one vote in the round (vote phase may end early)."""
    return len(latest_votes(votes, round, participants)) == len(participants)


def _leaders(scores: dict[str, int], among: Iterable[str]) -> list[str]:
    among = list(among)
    best = max(scores[c] for c in among)
    return [c for c in among if scores[c] == best]


def decide(votes: Sequence[Vote], participants: Sequence[str], seed: int) -> Decision:
    """Pick the Decision Maker.

    1. Most round-2 votes wins.
    2. If tied: most combined (round 1 + round 2) votes, considering only the round-2 leaders,
       so a candidate who lost round 2 cannot win on round-1 votes alone.
    3. If still tied: random.Random(seed).choice over the sorted tied ids (reproducible
       from the seed stored in the session).
    Missing votes are abstentions and simply add nothing.
    """
    tallies = {r: tally_round(votes, r, participants) for r in range(1, ROUNDS + 1)}
    final = tallies[ROUNDS].counts

    leaders = _leaders(final, participants)
    if len(leaders) == 1:
        return Decision(winner=leaders[0], method=TieBreak.ROUND_2, tallies=tallies)

    combined = {p: sum(t.counts[p] for t in tallies.values()) for p in participants}
    leaders = _leaders(combined, leaders)
    if len(leaders) == 1:
        return Decision(winner=leaders[0], method=TieBreak.COMBINED, tallies=tallies)

    winner = random.Random(seed).choice(sorted(leaders))
    return Decision(winner=winner, method=TieBreak.RANDOM, tallies=tallies)
