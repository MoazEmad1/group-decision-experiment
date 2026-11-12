"""Phase engine: timers, generation counter, voting, early vote end, results, AI join delay.
Drives Lobby + SessionManager directly on a temp SQLite DB with tiny durations."""

import asyncio
import time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.db import init_db
from app.lobby import Lobby
from app.logic import Phase
from app.session_manager import LiveSession, SessionManager
from tests.helpers import NO_AI_JOIN_DELAY, preset

FAST = {"chat_seconds": 0.05, "vote_seconds": 0.05}
LONG = 30  # "never fires during this test"


class FakeWS:
    """Stands in for a participant's WebSocket; records every frame the server sends."""

    def __init__(self):
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]

    def of_type(self, type_: str) -> list[dict]:
        return [f["payload"] for f in self.frames if f["type"] == type_]


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'phases.db'}")
    await init_db(engine)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    managers: list[SessionManager] = []

    class Env:
        db = sessionmaker

        def setup(self, config):
            self.lobby, self.manager = Lobby(config), SessionManager(config, sessionmaker)
            managers.append(self.manager)
            return self

        async def join(self) -> LiveSession:
            async with sessionmaker() as db:
                result = await self.lobby.join(db)
            await self.manager.on_join(result)
            return self.manager.get(result.session_id)

        async def rows(self, model, session: LiveSession, order_by=None):
            async with sessionmaker() as db:
                query = select(model).where(model.session_id == session.id)
                if order_by is not None:
                    query = query.order_by(order_by)
                return (await db.scalars(query)).all()

    yield Env()
    for manager in managers:
        await manager.close()
    await engine.dispose()


async def wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def start_3_humans(env, **timing) -> tuple[LiveSession, dict[str, FakeWS]]:
    env.setup(preset("3_humans", timing=timing))
    for _ in range(3):
        session = await env.join()
    sockets = {}
    for participant in session.participants.values():
        sockets[participant.label] = session.connections[participant.id] = FakeWS()
    return session, sockets


def by_label(session: LiveSession, label: str):
    return session.by_label(label)


async def vote(env, session, voter: str, target: str):
    return await env.manager.cast_vote(session, by_label(session, voter), target)


# --- timers ------------------------------------------------------------------


async def test_phases_advance_on_timers_to_results(env):
    session, sockets = await start_3_humans(env, **FAST)
    await wait_for(lambda: session.phase == Phase.RESULTS)

    events = await env.rows(models.PhaseEvent, session, order_by=models.PhaseEvent.id)
    assert [e.phase for e in events] == [
        "WAITING", "ROUND_1_CHAT", "ROUND_1_VOTE", "ROUND_2_CHAT", "ROUND_2_VOTE", "RESULTS",
    ]
    assert [e.deadline is not None for e in events] == [False, True, True, True, True, False]

    async with env.db() as db:
        row = await db.get(models.ExperimentSession, session.id)
    assert row.phase == "RESULTS"
    assert row.started_at is not None and row.ended_at is not None
    assert row.tiebreak_method == "random"  # nobody voted
    assert row.decision_maker_id in session.participants

    ws = sockets["A"]
    phases = [p["phase"] for p in ws.of_type("phase_changed")]
    assert phases == ["ROUND_1_VOTE", "ROUND_2_CHAT", "ROUND_2_VOTE", "RESULTS"]
    # Round-1 tally arrives right before round 2 starts; results come last.
    i = ws.types().index("vote_tally")
    assert ws.frames[i + 1]["payload"]["phase"] == "ROUND_2_CHAT"
    assert ws.of_type("vote_tally")[0] == {"round": 1, "counts": {"A": 0, "B": 0, "C": 0}, "abstentions": 3}
    assert ws.types()[-1] == "results"
    assert session.timer is None  # RESULTS has no timer


async def test_stale_timer_generation_is_ignored(env):
    session, _ = await start_3_humans(env, chat_seconds=LONG, vote_seconds=LONG)
    stale = session.generation

    async with session.lock:
        await env.manager._advance(session)  # ROUND_1_CHAT -> ROUND_1_VOTE by another path
    assert session.phase == Phase.ROUND_1_VOTE

    await env.manager.on_timer(session, stale)  # the old chat timer "fires" late
    assert session.phase == Phase.ROUND_1_VOTE  # ignored

    await env.manager.on_timer(session, session.generation)  # the current timer fires
    assert session.phase == Phase.ROUND_2_CHAT


# --- voting ------------------------------------------------------------------


async def test_vote_phase_ends_early_when_all_three_voted(env):
    session, sockets = await start_3_humans(env, chat_seconds=0.01, vote_seconds=LONG)
    await wait_for(lambda: session.phase == Phase.ROUND_1_VOTE)
    vote_timer = session.timer

    assert await vote(env, session, "A", "B") is None
    assert await vote(env, session, "B", "B") is None
    assert session.phase == Phase.ROUND_1_VOTE  # 2 of 3

    started = time.monotonic()
    assert await vote(env, session, "C", "A") is None
    assert session.phase == Phase.ROUND_2_CHAT  # advanced immediately, not after 30 s
    assert time.monotonic() - started < 1

    await wait_for(vote_timer.done)
    assert vote_timer.cancelled()  # old vote timer cancelled, not just ignored
    assert sockets["A"].of_type("vote_tally") == [
        {"round": 1, "counts": {"A": 1, "B": 2, "C": 0}, "abstentions": 0}
    ]


async def test_changed_vote_is_appended_and_latest_counts(env):
    session, sockets = await start_3_humans(env, chat_seconds=0.01, vote_seconds=LONG)
    await wait_for(lambda: session.phase == Phase.ROUND_1_VOTE)

    await vote(env, session, "A", "B")
    await vote(env, session, "A", "C")  # A changes their mind
    await vote(env, session, "B", "C")
    assert session.phase == Phase.ROUND_1_VOTE  # 3 rows, but only 2 distinct voters

    me = by_label(session, "A")
    assert env.manager.snapshot(session, me)["payload"]["my_vote"] == "C"
    assert sockets["A"].of_type("vote_recorded") == [{"round": 1, "target": "B"}, {"round": 1, "target": "C"}]
    assert sockets["B"].of_type("vote_recorded") == [{"round": 1, "target": "C"}]  # only to the voter

    await vote(env, session, "C", "A")
    assert session.phase == Phase.ROUND_2_CHAT
    assert session.tallies[1].counts == {"A": 1, "B": 0, "C": 2}

    rows = await env.rows(models.Vote, session, order_by=models.Vote.id)
    assert len(rows) == 4  # append-only
    labels = {p.id: p.label for p in session.participants.values()}
    assert [(labels[r.voter_id], labels[r.target_id]) for r in rows] == [
        ("A", "B"), ("A", "C"), ("B", "C"), ("C", "A"),
    ]


async def test_invalid_votes_rejected_and_not_stored(env):
    session, _ = await start_3_humans(env, chat_seconds=LONG, vote_seconds=LONG)
    assert await vote(env, session, "A", "B") == "voting_closed"  # still chatting

    async with session.lock:
        await env.manager._advance(session)
    assert await vote(env, session, "A", "Z") == "invalid_target"
    assert await env.rows(models.Vote, session) == []


async def test_results_decided_by_round_2_and_persisted(env):
    session, sockets = await start_3_humans(env, chat_seconds=0.01, vote_seconds=LONG)
    await wait_for(lambda: session.phase == Phase.ROUND_1_VOTE)
    for voter in "ABC":
        await vote(env, session, voter, "A")  # round 1: everyone picks A

    await wait_for(lambda: session.phase == Phase.ROUND_2_VOTE)
    me = by_label(session, "B")
    assert env.manager.snapshot(session, me)["payload"]["tallies"]["1"]["counts"] == {"A": 3, "B": 0, "C": 0}

    await vote(env, session, "A", "B")
    await vote(env, session, "B", "B")
    await vote(env, session, "C", "C")  # round 2: B wins outright
    assert session.phase == Phase.RESULTS

    expected = {
        "decision_maker": "B",
        "method": "round2",
        "tallies": {
            "1": {"round": 1, "counts": {"A": 3, "B": 0, "C": 0}, "abstentions": 0},
            "2": {"round": 2, "counts": {"A": 0, "B": 2, "C": 1}, "abstentions": 0},
        },
    }
    assert sockets["C"].of_type("results") == [expected]
    assert env.manager.snapshot(session, me)["payload"]["results"] == expected  # reconnect after the end

    async with env.db() as db:
        row = await db.get(models.ExperimentSession, session.id)
    assert row.decision_maker_id == by_label(session, "B").id
    assert row.tiebreak_method == "round2"
    assert await vote(env, session, "A", "A") == "voting_closed"  # nothing after RESULTS


async def test_missing_round_2_votes_count_as_abstentions(env):
    session, sockets = await start_3_humans(env, chat_seconds=0.01, vote_seconds=0.05)
    await wait_for(lambda: session.phase == Phase.ROUND_2_VOTE)
    await vote(env, session, "A", "C")  # only A votes in round 2, B and C abstain
    await wait_for(lambda: session.phase == Phase.RESULTS)

    result = sockets["A"].of_type("results")[0]
    assert result["decision_maker"] == "C"
    assert result["method"] == "round2"
    assert result["tallies"]["2"]["abstentions"] == 2


# --- waiting room / AI join delay --------------------------------------------


async def test_ai_join_delay_keeps_lone_human_waiting(env):
    env.setup(preset("1_human_2_ai", timing={"chat_seconds": LONG},
                     ai={"join_delay_min_seconds": 0.1, "join_delay_max_seconds": 0.2}))
    started = time.monotonic()
    session = await env.join()
    assert session.phase == Phase.WAITING  # human slots are full, but the AIs haven't "joined"

    await wait_for(lambda: session.phase == Phase.ROUND_1_CHAT, timeout=2)
    assert time.monotonic() - started >= 0.1

    rows = await env.rows(models.Participant, session)
    assert all(r.joined_at is not None for r in rows)
    human = next(r for r in rows if r.kind == "human")
    assert all(r.joined_at > human.joined_at for r in rows if r.kind == "ai")


async def test_zero_join_delay_starts_immediately(env):
    env.setup(preset("1_human_2_ai", timing={"chat_seconds": LONG}, ai=NO_AI_JOIN_DELAY))
    session = await env.join()
    assert session.phase == Phase.ROUND_1_CHAT


async def test_3_humans_start_when_third_joins(env):
    env.setup(preset("3_humans", timing={"chat_seconds": LONG}))  # join delay irrelevant: no AIs
    for expected in (Phase.WAITING, Phase.WAITING, Phase.ROUND_1_CHAT):
        session = await env.join()
        assert session.phase == expected


async def test_2_humans_1_ai_waits_for_second_human(env):
    env.setup(preset("2_humans_1_ai", timing={"chat_seconds": LONG}, ai=NO_AI_JOIN_DELAY))
    session = await env.join()
    assert session.phase == Phase.WAITING  # AI joined instantly, second human missing
    session = await env.join()
    assert session.phase == Phase.ROUND_1_CHAT
