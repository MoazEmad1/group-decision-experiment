"""Persistent research record. Everything needed to analyse or reproduce a session.

Timestamps are set in Python (UTC) at the moment the server handles the event, so the
in-memory state and the DB agree (e.g. vote cast_at used by the tally).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.logic import Phase


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# JSONB on Postgres, plain JSON elsewhere (SQLite in tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")
Timestamp = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class ExperimentSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    condition: Mapped[str] = mapped_column(String(64))
    config_snapshot: Mapped[dict] = mapped_column(JsonType)
    seed: Mapped[int] = mapped_column(BigInteger)  # tie-break draw: random.Random(seed)
    phase: Mapped[str] = mapped_column(String(16), default=Phase.WAITING.value, index=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(Timestamp)
    ended_at: Mapped[datetime | None] = mapped_column(Timestamp)
    # use_alter: sessions <-> participants reference each other.
    decision_maker_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("participants.id", use_alter=True, name="fk_sessions_decision_maker")
    )
    tiebreak_method: Mapped[str | None] = mapped_column(String(16))

    participants: Mapped[list["Participant"]] = relationship(
        back_populates="session",
        foreign_keys="Participant.session_id",
        order_by="Participant.slot_index",
    )


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("session_id", "label"), UniqueConstraint("session_id", "slot_index"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    slot_index: Mapped[int] = mapped_column(Integer)  # position in config.participants
    kind: Mapped[str] = mapped_column(String(8))  # "human" | "ai". Never sent to clients.
    label: Mapped[str] = mapped_column(String(1))  # "A" | "B" | "C", shuffled per session
    token: Mapped[str | None] = mapped_column(String(64), unique=True)  # humans only, set on join
    joined_at: Mapped[datetime | None] = mapped_column(Timestamp)  # humans: join time; AI: creation
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    session: Mapped[ExperimentSession] = relationship(back_populates="participants", foreign_keys=[session_id])


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"))
    round: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Vote(Base):
    """Append-only: every cast (including changes) is its own row. No UNIQUE on purpose;
    the tally uses each voter's latest row per round (see logic.latest_votes)."""

    __tablename__ = "votes"
    __table_args__ = (Index("ix_votes_session_round", "session_id", "round"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    round: Mapped[int] = mapped_column(Integer)
    voter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"))
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class PhaseEvent(Base):
    """Session timeline: one row per phase entered."""

    __tablename__ = "phase_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    phase: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    deadline: Mapped[datetime | None] = mapped_column(Timestamp)  # None for WAITING / RESULTS / ABORTED
