"""WebSocket message schema. Every frame is {"type": ..., "payload": {...}}.

Client -> server messages are validated with pydantic. Server -> client messages are built
only by the functions below, which take labels and never participant kind or internal ids:
this module is the single place to audit for blind-condition leaks.
"""

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from app.logic import RoundTally

MAX_MESSAGE_CHARS = 300


# --- client -> server ----------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SendMessagePayload(_Strict):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)]


class SendMessage(_Strict):
    type: Literal["send_message"]
    payload: SendMessagePayload


class CastVotePayload(_Strict):
    target: Annotated[str, StringConstraints(min_length=1, max_length=1)]


class CastVote(_Strict):
    type: Literal["cast_vote"]
    payload: CastVotePayload


ClientMessage = Annotated[Union[SendMessage, CastVote], Field(discriminator="type")]
_client_adapter = TypeAdapter(ClientMessage)


def parse_client_message(raw: str) -> SendMessage | CastVote:
    """Raises pydantic.ValidationError on bad JSON, unknown type or invalid payload."""
    return _client_adapter.validate_json(raw)


# --- server -> client ----------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _frame(type_: str, payload: dict) -> dict:
    return {"type": type_, "payload": payload}


def chat_entry(id: int, sender: str, round: int, text: str, ts: datetime) -> dict:
    return {"id": id, "from": sender, "round": round, "text": text, "ts": _iso(ts)}


def chat_message(entry: dict) -> dict:
    return _frame("chat_message", entry)


def phase_changed(phase: str, round: int | None, deadline: datetime | None, server_time: datetime) -> dict:
    return _frame(
        "phase_changed",
        {"phase": phase, "round": round, "deadline": _iso(deadline), "server_time": _iso(server_time)},
    )


def tally_payload(tally: RoundTally) -> dict:
    """Counts only (who voted for whom is never sent)."""
    return {"round": tally.round, "counts": dict(tally.counts), "abstentions": tally.abstentions}


def tallies_payload(tallies: dict[int, RoundTally]) -> dict[str, dict]:
    return {str(r): tally_payload(t) for r, t in sorted(tallies.items())}


def vote_tally(tally: RoundTally) -> dict:
    return _frame("vote_tally", tally_payload(tally))


def vote_recorded(round: int, target: str) -> dict:
    """Sent only to the voter, so their UI can show the current choice."""
    return _frame("vote_recorded", {"round": round, "target": target})


def results_payload(decision_maker: str, method: str, tallies: dict[int, RoundTally]) -> dict:
    return {"decision_maker": decision_maker, "method": method, "tallies": tallies_payload(tallies)}


def results(payload: dict) -> dict:
    return _frame("results", payload)


def state_snapshot(
    *,
    you: str,
    players: list[str],
    phase: str,
    round: int | None,
    deadline: datetime | None,
    server_time: datetime,
    messages: list[dict],
    my_vote: str | None,
    tallies: dict[int, RoundTally],
    results: dict | None,
) -> dict:
    return _frame(
        "state_snapshot",
        {
            "you": you,
            "players": players,
            "phase": phase,
            "round": round,
            "deadline": _iso(deadline),
            "server_time": _iso(server_time),
            "messages": messages,
            "my_vote": my_vote,
            "tallies": tallies_payload(tallies),  # completed rounds only
            "results": results,  # None until RESULTS
        },
    )


def error(code: str, message: str) -> dict:
    return _frame("error", {"code": code, "message": message})
