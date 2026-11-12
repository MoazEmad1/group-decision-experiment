"""Rule-based brain: short casual template messages that react to keywords and mentions,
and argue for one candidate. Works offline; the default ai.mode."""

import random
import re

from app.ai.base import Context, Reply, mentioned_labels

_SELF_NOMINATION = re.compile(
    r"\b(i\s+can(?!'t|not)|i\s+could|i'?ll\s+(do|take)|i\s+will\s+(do|take)|i'?d\s+(like|love|be\s+happy)\s+to"
    r"|i\s+want\s+to|i\s+volunteer|pick\s+me|vote\s+(for\s+)?me|happy\s+to\s+(do|take|lead))\b",
    re.IGNORECASE,
)
_GREETING = re.compile(r"^\s*(hi|hey|hello|hallo|yo|heya|morning)\b", re.IGNORECASE)

# {x} = candidate as typed by a person ("B", "player B", ...). *_SELF variants: the AI argues for itself.
OPEN_OTHER = [
    "hey all, I'd go with {x} tbh",
    "hi! I think {x} would be a good pick",
    "hey. {x} for decision maker?",
    "hello :) I'm leaning towards {x}",
    "hi, my vote would probably be {x}",
]
OPEN_SELF = [
    "hey all, I could do it if nobody else wants to",
    "hi! happy to be the decision maker tbh",
    "hey, I don't mind taking it",
    "hi :) I can do it if that's ok",
]
R2_LEADING_OTHER = ["{x} got the most votes, I'd stick with that", "ok so {x} it is? fine by me", "looks like {x}, I'm still in"]
R2_LEADING_SELF = ["guess I got the most votes, happy to do it", "ok I can take it then :)", "seems like it's me then, fine with that"]
R2_BANDWAGON = ["ok {x} got the most votes, I'm fine going with {x}", "fair enough, {x} then", "alright, I'll switch to {x}"]
R2_STICK_OTHER = ["I still think {x} would be good", "still going with {x} tbh", "hmm I'd still pick {x}"]
R2_STICK_SELF = ["I'd still be happy to do it", "still fine taking it myself tbh", "I can still do it if you want"]
AGREE_OTHER = ["yeah {x} makes sense", "agreed, {x}", "same, {x}", "+1 for {x}"]
AGREE_SELF = ["yeah I'm up for it", "sure, I can do it", "ok :) happy to"]
SWITCH_TO = ["ok fair, {x} works for me", "sure, {x} then", "fine with {x} too tbh"]
STICK_OTHER = ["hmm I'd still go {x}", "I think {x} would be better tbh", "not sure, I still like {x}"]
STICK_SELF = ["hmm I'd still like to do it tbh", "I could also do it though", "I'm still happy to take it"]
NOMINATED_ACCEPT = ["haha ok, I can do it", "sure, happy to", "oh ok, fine by me :)"]
NOMINATED_DECLINE = ["nah I'd rather {x} does it", "thanks but I think {x} fits better"]
QUESTION_OTHER = ["{x} maybe?", "idk, I'd say {x}", "probably {x}?"]
QUESTION_SELF = ["I could do it?", "idk, I can take it if needed"]
GREETING = ["hey", "hi :)", "hey there"]
FILLER_OTHER = ["{x} seems fine to me", "I'm good with {x}"]
FILLER_SELF = ["I'm happy to do it btw", "I can still do it"]


class RuleBasedBrain:
    SELF_CANDIDATE_PROBABILITY = 0.25
    SWITCH_TO_VOLUNTEER_PROBABILITY = 0.4
    SWITCH_TO_MENTIONED_PROBABILITY = 0.3
    ACCEPT_NOMINATION_PROBABILITY = 0.5
    BANDWAGON_PROBABILITY = 0.6
    FILLER_PROBABILITY = 0.5

    def __init__(self, rng: random.Random):
        self.rng = rng
        self._used: set[str] = set()

    def first_candidate(self, me: str, players: list[str]) -> str:
        others = [p for p in players if p != me]
        return me if self.rng.random() < self.SELF_CANDIDATE_PROBABILITY else self.rng.choice(others)

    async def open_round(self, ctx: Context) -> Reply | None:
        cand, me = ctx.candidate, ctx.me
        if ctx.round == 1 or not ctx.round1_counts:
            return self._say(OPEN_SELF if cand == me else OPEN_OTHER, cand)

        counts = ctx.round1_counts
        best = max(counts.values())
        leaders = [p for p, n in counts.items() if n == best]
        leader = leaders[0] if len(leaders) == 1 and best > 0 else None
        if leader == cand:
            return self._say(R2_LEADING_SELF if cand == me else R2_LEADING_OTHER, cand)
        if leader is not None and counts[cand] < best and self.rng.random() < self.BANDWAGON_PROBABILITY:
            return self._say(R2_LEADING_SELF if leader == me else R2_BANDWAGON, leader)
        return self._say(R2_STICK_SELF if cand == me else R2_STICK_OTHER, cand)

    async def respond(self, ctx: Context, message: dict) -> Reply | None:
        cand, me, sender, text = ctx.candidate, ctx.me, message["from"], message["text"]
        mentions = [m for m in mentioned_labels(text, ctx.players) if m != sender]
        rng = self.rng

        if me in mentions:  # someone nominates this AI
            if cand == me:
                return self._say(AGREE_SELF, me)
            if rng.random() < self.ACCEPT_NOMINATION_PROBABILITY:
                return self._say(NOMINATED_ACCEPT, me)
            return self._say(NOMINATED_DECLINE, cand)

        if mentions:
            named = mentions[0]
            if named == cand:
                return self._say(AGREE_SELF if cand == me else AGREE_OTHER, cand)
            if rng.random() < self.SWITCH_TO_MENTIONED_PROBABILITY:
                return self._say(SWITCH_TO, named)
            return self._say(STICK_SELF if cand == me else STICK_OTHER, cand)

        if _SELF_NOMINATION.search(text):  # sender volunteers
            if sender == cand:
                return self._say(AGREE_OTHER, cand)
            if rng.random() < self.SWITCH_TO_VOLUNTEER_PROBABILITY:
                return self._say(SWITCH_TO, sender)
            return self._say(STICK_SELF if cand == me else STICK_OTHER, cand)

        if "?" in text:
            return self._say(QUESTION_SELF if cand == me else QUESTION_OTHER, cand)
        if _GREETING.match(text):
            return self._say(GREETING, cand)
        if rng.random() < self.FILLER_PROBABILITY:
            return self._say(FILLER_SELF if cand == me else FILLER_OTHER, cand)
        return None

    def _say(self, templates: list[str], candidate: str) -> Reply:
        # Avoid repeating itself where possible; vary how the candidate is written, like people do.
        fresh = [t for t in templates if t not in self._used] or templates
        template = self.rng.choice(fresh)
        self._used.add(template)
        name = self.rng.choice([candidate, candidate, f"player {candidate}", f"Player {candidate}"])
        return Reply(text=template.format(x=name), candidate=candidate)
