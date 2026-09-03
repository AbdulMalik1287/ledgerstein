"""SQLite persistence for reconciliation runs.

A run is worth storing for one reason: the audit trail. An engine that can only
tell you what it decided while the process is alive is a script, not a control.
Everything here exists so a decision made last month can still be justified.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

DB_URL = os.environ.get("LEDGERSTEIN_DB_URL", "sqlite:///./ledgerstein.sqlite3")


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    duration_seconds: Mapped[float] = mapped_column(Float)
    rows: Mapped[int] = mapped_column(Integer)
    match_count: Mapped[int] = mapped_column(Integer)
    exception_count: Mapped[int] = mapped_column(Integer)
    exception_value_paise: Mapped[int] = mapped_column(Integer)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    scorecard: Mapped[dict] = mapped_column(JSON, default=dict)
    """Null when the batch shipped no ground truth, which is the real-world
    case -- a live merchant has no answer key."""

    matches: Mapped[list["MatchRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list["ExceptionRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    audit: Mapped[list["AuditRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class MatchRow(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    leg: Mapped[str] = mapped_column(String(48), index=True)
    left_id: Mapped[str] = mapped_column(String(64), index=True)
    right_id: Mapped[str] = mapped_column(String(64), index=True)
    tier: Mapped[str] = mapped_column(String(24), index=True)
    rule: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    amount_paise: Mapped[int] = mapped_column(Integer)

    run: Mapped[Run] = relationship(back_populates="matches")


class ExceptionRow(Base):
    __tablename__ = "exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    exception_type: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(Text)
    amount_paise: Mapped[int] = mapped_column(Integer)
    leg: Mapped[str] = mapped_column(String(48))
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolution: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[str] = mapped_column(String(64), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped[Run] = relationship(back_populates="exceptions")


class AuditRow(Base):
    __tablename__ = "audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    at: Mapped[datetime] = mapped_column(DateTime)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(24), index=True)
    leg: Mapped[str] = mapped_column(String(48))
    subject: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    run: Mapped[Run] = relationship(back_populates="audit")


_engine = create_engine(DB_URL, future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(_engine)


def persist(result, scorecard: dict | None = None) -> str:
    """Write a completed run and return its id.

    The audit rows go in with the run in one transaction. A half-written trail
    is worse than none, because it looks complete.
    """
    with SessionLocal() as session:
        run = Run(
            id=result.run_id,
            batch=result.batch,
            started_at=result.started_at,
            duration_seconds=result.duration_seconds,
            rows=result.sources.row_count(),
            match_count=len(result.matches),
            exception_count=len(result.exceptions),
            exception_value_paise=result.exception_value_paise(),
            llm_calls=result.llm_calls,
            scorecard=scorecard or {},
        )
        session.add(run)
        session.add_all(
            MatchRow(
                run_id=run.id,
                leg=str(m.leg),
                left_id=m.left_id,
                right_id=m.right_id,
                tier=str(m.tier),
                rule=m.rule,
                reason=m.reason,
                confidence=m.confidence,
                amount_paise=m.amount_paise,
            )
            for m in result.matches
        )
        session.add_all(
            ExceptionRow(
                run_id=run.id,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                exception_type=e.exception_type,
                reason=e.reason,
                amount_paise=e.amount_paise,
                leg=e.leg,
                candidates=list(e.candidates),
            )
            for e in result.exceptions
        )
        session.add_all(
            AuditRow(
                run_id=run.id,
                sequence=a.sequence,
                at=a.at,
                actor=a.actor,
                action=a.action,
                leg=a.leg,
                subject=a.subject,
                detail=a.detail,
                confidence=a.confidence,
            )
            for a in result.audit
        )
        session.commit()
        return run.id


def get_session() -> Session:
    return SessionLocal()


__all__ = [
    "AuditRow",
    "Base",
    "ExceptionRow",
    "MatchRow",
    "Run",
    "get_session",
    "init_db",
    "persist",
    "select",
]
