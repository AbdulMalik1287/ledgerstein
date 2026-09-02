"""End-to-end checks over the HTTP surface, against a throwaway database.

These run the real engine on the real generated batch. The point is not to
re-test the matcher -- that is covered elsewhere -- but to prove the API hands
back what the engine actually decided, and that a human resolution is recorded
as carefully as a machine one.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BATCH_DIR = Path(__file__).resolve().parents[2] / "data" / "generated" / "batch_a"

pytestmark = pytest.mark.skipif(
    not (BATCH_DIR / "bank_statement.csv").exists(),
    reason="run `python -m app.gen.generate --out ../data/generated/batch_a` first",
)


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module=None):
    """A client bound to a fresh SQLite file per test session."""
    db_path = tmp_path_factory.mktemp("kosh") / "test.sqlite3"
    import os

    os.environ["KOSH_DB_URL"] = "sqlite:///%s" % db_path.as_posix()

    from app import db as db_module

    importlib.reload(db_module)
    from app import main as main_module

    importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def run_id(client) -> str:
    response = client.post("/api/runs", json={"batch": "batch_a"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_health_finds_the_batches(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["batches"] >= 1


def test_unknown_batch_is_a_404(client):
    assert client.post("/api/runs", json={"batch": "nope"}).status_code == 404


def test_a_run_reports_what_the_engine_produced(client, run_id):
    body = client.get("/api/runs/%s" % run_id).json()
    summary, card = body["summary"], body["scorecard"]

    assert summary["rows"] > 500
    assert summary["match_count"] > 400
    assert summary["exception_count"] > 0
    assert summary["llm_calls"] == 0

    # The headline claim, asserted rather than described: nothing was matched
    # wrongly, and no rupees were lost to a false match.
    assert card["overall"]["precision"] == 1.0
    assert card["overall"]["wrong"] == 0
    assert card["overall"]["wrong_value_rupees"] == 0.0
    assert card["overall"]["recall"] > 0.95


def test_every_match_carries_a_rule_and_a_reason(client, run_id):
    """No unexplained matches. This is the product, not a nicety."""
    items = client.get("/api/runs/%s/matches?limit=2000" % run_id).json()["items"]
    assert items
    for item in items:
        assert item["rule"], item
        assert len(item["reason"]) > 20, item
        assert 0 < item["confidence"] <= 1.0, item


def test_matches_can_be_filtered_by_tier(client, run_id):
    body = client.get("/api/runs/%s/matches?tier=T1_EXACT" % run_id).json()
    assert body["total"] > 0
    assert {i["tier"] for i in body["items"]} == {"T1_EXACT"}


def test_the_exception_queue_leads_with_the_biggest_exposure(client, run_id):
    items = client.get("/api/runs/%s/exceptions?limit=50" % run_id).json()["items"]
    amounts = [i["amount_rupees"] for i in items]
    assert amounts == sorted(amounts, reverse=True)


def test_ambiguous_rows_carry_their_candidates(client, run_id):
    body = client.get(
        "/api/runs/%s/exceptions?exception_type=AMBIGUOUS" % run_id
    ).json()
    assert body["total"] > 0, "the batch should contain undecidable rows"
    for item in body["items"]:
        assert len(item["candidates"]) >= 2, item


def test_the_summary_totals_agree_with_the_queue(client, run_id):
    summary = client.get("/api/runs/%s/exception-summary" % run_id).json()
    queue_total = client.get("/api/runs/%s/exceptions?limit=1" % run_id).json()["total"]
    assert sum(row["count"] for row in summary) == queue_total
    assert summary == sorted(summary, key=lambda r: -r["value_rupees"])


def test_resolving_an_exception_writes_to_the_audit_trail(client, run_id):
    queue = client.get("/api/runs/%s/exceptions?limit=1" % run_id).json()["items"]
    target = queue[0]

    response = client.post(
        "/api/exceptions/%d/resolve" % target["id"],
        json={
            "resolution": "Confirmed by phone with the customer.",
            "resolved_by": "abdul",
            "link_to": "INV-2026-0001",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"

    trail = client.get("/api/runs/%s/audit?action=resolve" % run_id).json()
    assert trail["total"] >= 1
    event = trail["items"][-1]
    assert event["actor"] == "human:abdul"
    assert "INV-2026-0001" in event["detail"]

    # A resolution is final. Re-resolving would silently rewrite history.
    again = client.post(
        "/api/exceptions/%d/resolve" % target["id"], json={"resolution": "again"}
    )
    assert again.status_code == 409


def test_the_audit_trail_covers_the_whole_run(client, run_id):
    trail = client.get("/api/runs/%s/audit?limit=5000" % run_id).json()
    sequences = [item["sequence"] for item in trail["items"]]

    assert sequences == sorted(sequences), "the trail must be append-only in order"
    actions = {item["action"] for item in trail["items"]}
    assert {"start", "match", "flag", "finish"} <= actions


def test_audit_can_be_filtered_to_one_subject(client, run_id):
    match = client.get("/api/runs/%s/matches?limit=1" % run_id).json()["items"][0]
    trail = client.get(
        "/api/runs/%s/audit?subject=%s" % (run_id, match["left_id"])
    ).json()
    assert trail["total"] >= 1
    assert all(match["left_id"] in i["subject"] for i in trail["items"])
