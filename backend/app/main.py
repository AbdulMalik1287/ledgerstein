"""HTTP surface for LedgerStein.

Deliberately small. The dashboard needs to start a run, read what it produced,
work the exception queue, and pull the audit trail for any row. Everything else
belongs in the engine.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .db import AuditRow, ExceptionRow, MatchRow, Run, get_session, init_db, persist
from .gen.generate import generate, write_batch
from .recon.engine import reconcile_directory
from .recon.metrics import load_truth, score

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "generated"

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

DEMO_BATCHES = (
    # name, seed, invoices, customers
    ("batch_a", 7, 240, 18),
    ("batch_b", 4291, 260, 21),
)


def ensure_demo_data() -> None:
    """Make the service self-sufficient on a cold, empty deployment.

    The generator is deterministic, so the batches are rebuilt from their seeds
    rather than committed -- a fresh container produces byte-identical data to
    the one this was developed against. Then one batch is reconciled so the
    dashboard opens showing real work instead of an empty shell.
    """
    for name, seed, invoices, customers in DEMO_BATCHES:
        directory = DATA_ROOT / name
        if (directory / "bank_statement.csv").exists():
            continue
        write_batch(
            generate(
                seed=seed,
                invoices=invoices,
                customers=customers,
                start=date(2026, 6, 1),
                days=45,
                issue_days=30,
                name=name,
            ),
            directory,
        )

    with get_session() as session:
        if session.scalar(select(func.count()).select_from(Run)):
            return
    directory = DATA_ROOT / "batch_b"
    if not (directory / "bank_statement.csv").exists():
        return
    result = reconcile_directory(directory)
    card = None
    if (directory / "truth.json").exists():
        card = score(result, load_truth(directory)).as_dict()
    persist(result, card)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ensure_demo_data()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="LedgerStein",
    description="AI Finance Controller: three-way reconciliation with an "
    "honest exception list.",
    version="0.1.0",
)

# The dashboard is served from a separate dev server, so it needs CORS. Kept
# to localhost origins rather than "*" -- this API can resolve exceptions.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------- schemas


class RunRequest(BaseModel):
    batch: str = Field(default="batch_a", description="Directory under data/generated")
    use_llm: bool = Field(
        default=False, description="Offer the ambiguous residue to the adjudicator"
    )
    provider: str = Field(
        default="auto", description="auto | anthropic | gemini | groq | ollama"
    )
    model: str = Field(default="", description="Override the backend's default")
    max_llm_calls: int = 25


class RunSummary(BaseModel):
    id: str
    batch: str
    started_at: datetime
    duration_seconds: float
    rows: int
    match_count: int
    exception_count: int
    exception_value_rupees: float
    llm_calls: int
    has_scorecard: bool

    @staticmethod
    def of(run: Run) -> "RunSummary":
        return RunSummary(
            id=run.id,
            batch=run.batch,
            started_at=run.started_at,
            duration_seconds=run.duration_seconds,
            rows=run.rows,
            match_count=run.match_count,
            exception_count=run.exception_count,
            exception_value_rupees=run.exception_value_paise / 100,
            llm_calls=run.llm_calls,
            has_scorecard=bool(run.scorecard),
        )


class ResolveRequest(BaseModel):
    resolution: str = Field(description="What the human decided and why")
    resolved_by: str = Field(default="controller")
    link_to: str = Field(
        default="", description="Optional id this row was manually matched to"
    )


# --------------------------------------------------------------------- runs


@app.get("/api/batches")
def list_batches() -> list[dict]:
    """Batches available on disk, and whether they carry an answer key."""
    if not DATA_ROOT.exists():
        return []
    out = []
    for directory in sorted(DATA_ROOT.iterdir()):
        if not (directory / "bank_statement.csv").exists():
            continue
        out.append(
            {
                "name": directory.name,
                "has_truth": (directory / "truth.json").exists(),
            }
        )
    return out


@app.post("/api/runs", response_model=RunSummary)
def create_run(request: RunRequest) -> RunSummary:
    directory = DATA_ROOT / request.batch
    if not (directory / "bank_statement.csv").exists():
        raise HTTPException(404, "No batch named %r" % request.batch)

    adjudicator = None
    if request.use_llm:
        from .recon.adjudicator import Adjudicator

        adjudicator = Adjudicator(
            provider=request.provider,
            model=request.model,
            max_calls=request.max_llm_calls,
        )

    result = reconcile_directory(directory, adjudicator=adjudicator)
    card = None
    if (directory / "truth.json").exists():
        card = score(result, load_truth(directory)).as_dict()

    persist(result, card)
    with get_session() as session:
        return RunSummary.of(session.get(Run, result.run_id))


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs(limit: int = Query(default=25, le=100)) -> list[RunSummary]:
    with get_session() as session:
        runs = session.scalars(
            select(Run).order_by(Run.started_at.desc()).limit(limit)
        ).all()
        return [RunSummary.of(r) for r in runs]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    with get_session() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "No run %r" % run_id)
        return {
            "summary": RunSummary.of(run).model_dump(),
            "scorecard": run.scorecard or None,
        }


@app.get("/api/runs/{run_id}/matches")
def list_matches(
    run_id: str,
    leg: str | None = None,
    tier: str | None = None,
    limit: int = Query(default=200, le=2000),
    offset: int = 0,
) -> dict:
    with get_session() as session:
        stmt = select(MatchRow).where(MatchRow.run_id == run_id)
        if leg:
            stmt = stmt.where(MatchRow.leg == leg)
        if tier:
            stmt = stmt.where(MatchRow.tier == tier)
        total = session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        rows = session.scalars(
            stmt.order_by(MatchRow.id).limit(limit).offset(offset)
        ).all()
        return {
            "total": total,
            "items": [
                {
                    "leg": r.leg,
                    "left_id": r.left_id,
                    "right_id": r.right_id,
                    "tier": r.tier,
                    "rule": r.rule,
                    "reason": r.reason,
                    "confidence": r.confidence,
                    "amount_rupees": r.amount_paise / 100,
                }
                for r in rows
            ],
        }


@app.get("/api/runs/{run_id}/exceptions")
def list_exceptions(
    run_id: str,
    exception_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=200, le=2000),
    offset: int = 0,
) -> dict:
    with get_session() as session:
        stmt = select(ExceptionRow).where(ExceptionRow.run_id == run_id)
        if exception_type:
            stmt = stmt.where(ExceptionRow.exception_type == exception_type)
        if status:
            stmt = stmt.where(ExceptionRow.status == status)
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        # Biggest rupee exposure first. A queue sorted by insertion order buries
        # the one row that actually matters under forty small ones.
        rows = session.scalars(
            stmt.order_by(ExceptionRow.amount_paise.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return {
            "total": total,
            "items": [_exception_json(r) for r in rows],
        }


@app.get("/api/runs/{run_id}/exception-summary")
def exception_summary(run_id: str) -> list[dict]:
    """Counts and rupee exposure per exception type, worst first."""
    with get_session() as session:
        rows = session.execute(
            select(
                ExceptionRow.exception_type,
                func.count().label("count"),
                func.sum(ExceptionRow.amount_paise).label("value"),
            )
            .where(ExceptionRow.run_id == run_id)
            .group_by(ExceptionRow.exception_type)
        ).all()
        return sorted(
            [
                {
                    "exception_type": r.exception_type,
                    "count": r.count,
                    "value_rupees": (r.value or 0) / 100,
                }
                for r in rows
            ],
            key=lambda d: -d["value_rupees"],
        )


@app.post("/api/exceptions/{exception_id}/resolve")
def resolve_exception(exception_id: int, request: ResolveRequest) -> dict:
    """Close an exception by hand, and record who did it.

    The manual resolution is written to the same audit trail as the engine's
    own decisions. A human overriding the machine is a decision like any other
    and has to be as reviewable.
    """
    with get_session() as session:
        row = session.get(ExceptionRow, exception_id)
        if row is None:
            raise HTTPException(404, "No exception %d" % exception_id)
        if row.status == "resolved":
            raise HTTPException(409, "Exception %d is already resolved" % exception_id)

        row.status = "resolved"
        row.resolution = request.resolution
        row.resolved_by = request.resolved_by
        row.resolved_at = datetime.now()

        next_sequence = (
            session.scalar(
                select(func.max(AuditRow.sequence)).where(
                    AuditRow.run_id == row.run_id
                )
            )
            or 0
        ) + 1
        session.add(
            AuditRow(
                run_id=row.run_id,
                sequence=next_sequence,
                at=row.resolved_at,
                actor="human:%s" % request.resolved_by,
                action="resolve",
                leg=row.leg,
                subject=row.entity_id,
                detail="%s resolved as: %s%s"
                % (
                    row.exception_type,
                    request.resolution,
                    " (linked to %s)" % request.link_to if request.link_to else "",
                ),
            )
        )
        session.commit()
        return _exception_json(row)


@app.get("/api/runs/{run_id}/audit")
def list_audit(
    run_id: str,
    subject: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    limit: int = Query(default=300, le=5000),
    offset: int = 0,
) -> dict:
    with get_session() as session:
        stmt = select(AuditRow).where(AuditRow.run_id == run_id)
        if subject:
            stmt = stmt.where(AuditRow.subject.contains(subject))
        if actor:
            stmt = stmt.where(AuditRow.actor.contains(actor))
        if action:
            stmt = stmt.where(AuditRow.action == action)
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = session.scalars(
            stmt.order_by(AuditRow.sequence).limit(limit).offset(offset)
        ).all()
        return {
            "total": total,
            "items": [
                {
                    "sequence": r.sequence,
                    "at": r.at.isoformat(sep=" ", timespec="seconds"),
                    "actor": r.actor,
                    "action": r.action,
                    "leg": r.leg,
                    "subject": r.subject,
                    "detail": r.detail,
                    "confidence": r.confidence,
                }
                for r in rows
            ],
        }


def _exception_json(row: ExceptionRow) -> dict:
    return {
        "id": row.id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "exception_type": row.exception_type,
        "reason": row.reason,
        "amount_rupees": row.amount_paise / 100,
        "leg": row.leg,
        "candidates": row.candidates or [],
        "status": row.status,
        "resolution": row.resolution,
        "resolved_by": row.resolved_by,
        "resolved_at": row.resolved_at.isoformat(sep=" ", timespec="seconds")
        if row.resolved_at
        else None,
    }


@app.get("/api/health")
def health() -> dict:
    from .recon import providers

    return {
        "status": "ok",
        "data_root": str(DATA_ROOT),
        "batches": len(list_batches()),
        # Which model backends have a key present. Empty is a valid state: the
        # adjudicator then skips and says so rather than guessing.
        "llm_backends": providers.available(),
    }


# Mounted last so every /api route above wins the match. ``html=True`` serves
# index.html for unknown paths, which keeps a refresh on any dashboard tab from
# 404-ing.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="dashboard")
