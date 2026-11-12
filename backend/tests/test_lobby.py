"""Join / slot assignment. Runs on a temp SQLite file so no Postgres is needed;
production uses the same models on Postgres (JSON column becomes JSONB)."""

import asyncio
from collections import Counter

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import ExperimentConfig
from app.db import abort_unfinished_sessions, init_db
from app.lobby import LABELS, SEED_RANGE, Lobby
from app.models import ExperimentSession, Participant, PhaseEvent
from tests.helpers import preset


@pytest.fixture
async def sessionmaker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await init_db(engine)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def join(lobby: Lobby, sessionmaker):
    # One DB session per call, like one per HTTP request.
    async with sessionmaker() as db:
        return await lobby.join(db)


async def participants_of(sessionmaker, session_id):
    async with sessionmaker() as db:
        rows = await db.scalars(
            select(Participant).where(Participant.session_id == session_id).order_by(Participant.slot_index)
        )
        return rows.all()


async def test_1_human_2_ai_session_created_with_ai_rows_not_yet_joined(sessionmaker):
    result = await join(Lobby(preset("1_human_2_ai")), sessionmaker)
    assert result.humans_full
    assert result.label in LABELS

    rows = await participants_of(sessionmaker, result.session_id)
    assert [r.kind for r in rows] == ["human", "ai", "ai"]
    assert sorted(r.label for r in rows) == list(LABELS)
    assert all(r.token is None for r in rows if r.kind == "ai")
    assert all((r.joined_at is not None) == (r.kind == "human") for r in rows)  # AIs join later, after their delay


async def test_3_humans_fill_one_session_then_4th_starts_new(sessionmaker):
    lobby = Lobby(preset("3_humans"))
    results = [await join(lobby, sessionmaker) for _ in range(4)]

    first_three = results[:3]
    assert len({r.session_id for r in first_three}) == 1
    assert sorted(r.label for r in first_three) == list(LABELS)
    assert [r.humans_full for r in first_three] == [False, False, True]

    fourth = results[3]
    assert fourth.session_id != first_three[0].session_id
    assert not fourth.humans_full


async def test_2_humans_1_ai_two_joins_fill_session(sessionmaker):
    lobby = Lobby(preset("2_humans_1_ai"))
    a, b = await join(lobby, sessionmaker), await join(lobby, sessionmaker)
    assert a.session_id == b.session_id
    assert (a.humans_full, b.humans_full) == (False, True)
    assert a.label != b.label


async def test_concurrent_joins_never_overfill(sessionmaker):
    lobby = Lobby(preset("3_humans"))
    results = await asyncio.gather(*(join(lobby, sessionmaker) for _ in range(7)))

    assert len({r.token for r in results}) == 7
    sizes = sorted(Counter(r.session_id for r in results).values(), reverse=True)
    assert sizes == [3, 3, 1]
    for session_id in {r.session_id for r in results}:
        labels = [r.label for r in results if r.session_id == session_id]
        assert len(labels) == len(set(labels))  # no label handed out twice


async def test_session_stores_config_snapshot_seed_and_waiting_event(sessionmaker):
    config = preset("2_humans_1_ai")
    result = await join(Lobby(config), sessionmaker)

    async with sessionmaker() as db:
        session = await db.get(ExperimentSession, result.session_id)
        events = (await db.scalars(select(PhaseEvent).where(PhaseEvent.session_id == session.id))).all()

    assert session.condition == "2_humans_1_ai"
    assert session.config_snapshot == config.snapshot()
    assert ExperimentConfig.model_validate(session.config_snapshot) == config
    assert 0 <= session.seed < SEED_RANGE
    assert session.phase == "WAITING"
    assert [e.phase for e in events] == ["WAITING"]


async def test_labels_are_shuffled_per_session(sessionmaker):
    lobby = Lobby(preset("1_human_2_ai"))
    human_labels = {(await join(lobby, sessionmaker)).label for _ in range(30)}
    # The single human sits in slot 0; if labels weren't shuffled it would always be "A".
    # P(same label 30 times | uniform shuffle) = 3 * (1/3)^30.
    assert len(human_labels) > 1


async def test_abort_unfinished_sessions_and_rejoin_gets_new_session(sessionmaker):
    lobby = Lobby(preset("3_humans"))
    stale = await join(lobby, sessionmaker)

    async with sessionmaker() as db:
        assert await abort_unfinished_sessions(db) == 1
    async with sessionmaker() as db:
        session = await db.get(ExperimentSession, stale.session_id)
        assert session.phase == "ABORTED"
        assert session.ended_at is not None

    fresh = await join(lobby, sessionmaker)
    assert fresh.session_id != stale.session_id

    async with sessionmaker() as db:
        assert await abort_unfinished_sessions(db) == 1  # only the fresh one; ABORTED is terminal
