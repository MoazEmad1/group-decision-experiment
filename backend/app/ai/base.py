"""The Brain interface: what an AI says and whom it argues for.

When the AI acts (delays, caps, vote timing) lives in agent.py and is the same for
every brain. Step 9's LLM brain implements this same interface.
"""

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Context:
    me: str  # own label
    players: list[str]  # all labels, sorted
    round: int
    candidate: str  # whom this AI currently argues for
    messages: list[dict]  # full chat history (ws_schema.chat_entry dicts)
    round1_counts: dict[str, int] | None  # round-1 vote counts, available in round 2


@dataclass(frozen=True)
class Reply:
    text: str
    candidate: str  # whom this message argues for; becomes the AI's candidate once it is posted


class Brain(Protocol):
    def first_candidate(self, me: str, players: list[str]) -> str: ...

    async def open_round(self, ctx: Context) -> Reply | None: ...

    async def respond(self, ctx: Context, message: dict) -> Reply | None: ...


_PLAYER_REF = re.compile(r"\bplayer\s*([abc])\b", re.IGNORECASE)
_BARE_LETTER = re.compile(r"(?<![\w'])([ABC])(?![\w'])")


def mentioned_labels(text: str, players: list[str]) -> list[str]:
    """Labels referred to in a message: "player b", "Player B" or a bare capital "B".
    A bare "A" followed by a lowercase word is treated as the article ("A good idea")."""
    found = [m.group(1).upper() for m in _PLAYER_REF.finditer(text)]
    for m in _BARE_LETTER.finditer(text):
        if m.group(1) == "A" and re.match(r"\s+[a-z]", text[m.end():]):
            continue
        found.append(m.group(1))
    return [label for label in dict.fromkeys(found) if label in players]
