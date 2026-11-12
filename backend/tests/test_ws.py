"""WebSocket: token auth, state_snapshot, chat broadcast + persistence, reconnects.
Full app via TestClient on a temp SQLite DB."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.models import Message, Participant
from app.session_manager import CLOSE_INVALID_TOKEN, CLOSE_REPLACED
from app.ws_schema import MAX_MESSAGE_CHARS
from tests.helpers import preset

SNAPSHOT_KEYS = {"you", "players", "phase", "round", "deadline", "server_time", "messages", "my_vote", "tallies"}


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(preset_name: str) -> TestClient:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'{len(clients)}.db'}")
        client = TestClient(create_app(config=preset(preset_name), engine=engine))
        client.__enter__()  # runs the lifespan (init_db, lobby, manager)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def join(client: TestClient) -> dict:
    response = client.post("/api/join")
    assert response.status_code == 200
    return response.json()


def connect(client: TestClient, token: str):
    return client.websocket_connect(f"/ws?token={token}")


def send_chat(ws, text: str) -> None:
    ws.send_json({"type": "send_message", "payload": {"text": text}})


def db_rows(client: TestClient, model):
    async def fetch():
        async with client.app.state.sessionmaker() as db:
            return (await db.scalars(select(model))).all()

    return client.portal.call(fetch)


def assert_no_leaks(client: TestClient, frame: dict) -> None:
    """Blind condition: no participant kind, no internal participant ids."""

    def walk(node):
        if isinstance(node, dict):
            assert "kind" not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            assert node not in ("human", "ai")

    walk(frame)
    raw = json.dumps(frame)
    for participant in db_rows(client, Participant):
        assert str(participant.id) not in raw
        assert participant.id.hex not in raw


# --- auth --------------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "not-a-real-token"])
def test_invalid_token_is_closed(make_client, token):
    client = make_client("1_human_2_ai")
    with connect(client, token) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == CLOSE_INVALID_TOKEN


def test_join_response_has_only_token_and_label(make_client):
    body = join(make_client("1_human_2_ai"))
    assert set(body) == {"token", "label"}


# --- snapshot ------------------------------------------------------------------


def test_snapshot_on_connect(make_client):
    client = make_client("1_human_2_ai")  # the single human fills the session -> round 1 starts
    me = join(client)
    with connect(client, me["token"]) as ws:
        frame = ws.receive_json()

    assert frame["type"] == "state_snapshot"
    snap = frame["payload"]
    assert set(snap) == SNAPSHOT_KEYS
    assert snap["you"] == me["label"]
    assert snap["players"] == ["A", "B", "C"]
    assert snap["phase"] == "ROUND_1_CHAT"
    assert snap["round"] == 1
    assert snap["deadline"] is not None and snap["server_time"] is not None
    assert snap["messages"] == []
    assert_no_leaks(client, frame)


def test_waiting_snapshot_and_phase_changed_when_session_fills(make_client):
    client = make_client("2_humans_1_ai")
    first = join(client)
    with connect(client, first["token"]) as ws:
        snap = ws.receive_json()["payload"]
        assert snap["phase"] == "WAITING"
        assert snap["deadline"] is None

        join(client)  # second human fills the session
        frame = ws.receive_json()
        assert frame["type"] == "phase_changed"
        assert frame["payload"]["phase"] == "ROUND_1_CHAT"
        assert frame["payload"]["round"] == 1
        assert frame["payload"]["deadline"] is not None


# --- chat ----------------------------------------------------------------------


def test_chat_broadcast_to_all_and_persisted(make_client):
    client = make_client("2_humans_1_ai")
    a, b = join(client), join(client)
    with connect(client, a["token"]) as ws_a, connect(client, b["token"]) as ws_b:
        ws_a.receive_json(), ws_b.receive_json()  # snapshots

        send_chat(ws_a, "  I think B should lead  ")
        got_a, got_b = ws_a.receive_json(), ws_b.receive_json()

    assert got_a == got_b  # sender gets the same server echo as everyone else
    assert got_a["type"] == "chat_message"
    msg = got_a["payload"]
    assert set(msg) == {"id", "from", "round", "text", "ts"}
    assert msg["from"] == a["label"]
    assert msg["round"] == 1
    assert msg["text"] == "I think B should lead"  # stripped
    assert_no_leaks(client, got_a)

    rows = db_rows(client, Message)
    assert len(rows) == 1
    assert rows[0].id == msg["id"]
    assert rows[0].text == "I think B should lead"
    assert rows[0].round == 1
    assert rows[0].created_at is not None


def test_chat_rejected_while_waiting(make_client):
    client = make_client("3_humans")
    me = join(client)
    with connect(client, me["token"]) as ws:
        ws.receive_json()
        send_chat(ws, "hello?")
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["payload"]["code"] == "chat_closed"
    assert db_rows(client, Message) == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"type": "dance", "payload": {}}),
        json.dumps({"type": "send_message", "payload": {"text": "   "}}),
        json.dumps({"type": "send_message", "payload": {"text": "x" * (MAX_MESSAGE_CHARS + 1)}}),
        json.dumps({"type": "send_message", "payload": {"text": "hi", "kind": "ai"}}),
    ],
)
def test_invalid_client_message_returns_error_and_keeps_connection(make_client, raw):
    client = make_client("1_human_2_ai")
    me = join(client)
    with connect(client, me["token"]) as ws:
        ws.receive_json()
        ws.send_text(raw)
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["payload"]["code"] == "invalid_message"

        send_chat(ws, "still here")  # connection survived
        assert ws.receive_json()["type"] == "chat_message"


def test_vote_outside_vote_phase_rejected(make_client):
    client = make_client("1_human_2_ai")
    me = join(client)
    with connect(client, me["token"]) as ws:
        ws.receive_json()
        ws.send_json({"type": "cast_vote", "payload": {"target": "A"}})
        frame = ws.receive_json()
    assert frame["payload"]["code"] == "voting_closed"


# --- reconnect -----------------------------------------------------------------


def test_reconnect_snapshot_contains_history(make_client):
    client = make_client("1_human_2_ai")
    me = join(client)
    with connect(client, me["token"]) as ws:
        ws.receive_json()
        send_chat(ws, "first")
        send_chat(ws, "second")
        ws.receive_json(), ws.receive_json()

    with connect(client, me["token"]) as ws:
        snap = ws.receive_json()["payload"]
    assert snap["phase"] == "ROUND_1_CHAT"
    assert [m["text"] for m in snap["messages"]] == ["first", "second"]
    assert all(m["from"] == me["label"] for m in snap["messages"])


def test_second_connection_replaces_first(make_client):
    client = make_client("1_human_2_ai")
    me = join(client)
    with connect(client, me["token"]) as old:
        old.receive_json()
        with connect(client, me["token"]) as new:
            new.receive_json()
            with pytest.raises(WebSocketDisconnect) as closed:
                old.receive_json()
            assert closed.value.code == CLOSE_REPLACED

            send_chat(new, "new tab works")
            assert new.receive_json()["payload"]["text"] == "new tab works"
