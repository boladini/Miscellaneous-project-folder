"""AC8/AC10 API: auth, CSRF, route coverage, decisions, tenant isolation; demo end-to-end."""
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lab.api.app import app
from lab.models import Proposal, User

from .test_engine import make_proposal


def login(client, email="owner@example.com", pw="pw-owner-123"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"x-csrf-token": r.json()["csrf_token"]}


@pytest.fixture()
def client(db, user):
    return TestClient(app)


def test_auth_required_and_structured_errors(client):
    r = client.get("/api/v1/dashboard")
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "unauthenticated" and err["correlation_id"]
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong"}).status_code == 401


def test_csrf_enforced_on_mutations(client):
    h = login(client)
    assert client.post("/api/v1/jobs/market-refresh").status_code == 403
    assert client.post("/api/v1/jobs/market-refresh", headers={"x-csrf-token": "bad"}).status_code == 403
    assert client.post("/api/v1/jobs/market-refresh", headers=h).status_code == 202


def test_all_routes_respond(client, db, user):
    h = login(client)
    pr = make_proposal(db, user)
    pid = pr.portfolio_id
    for path in ["/api/v1/auth/me", "/api/v1/dashboard", "/api/v1/dashboard?start=2026-01-01&end=2026-12-31", "/api/v1/portfolios",
                 f"/api/v1/portfolios/{pid}", f"/api/v1/portfolios/{pid}/series", "/api/v1/proposals?status=pending",
                 f"/api/v1/proposals/{pr.id}", "/api/v1/research/runs", "/api/v1/evidence?q=reversal", "/api/v1/research/rejected",
                 "/api/v1/backtests", "/api/v1/journal", "/api/v1/notifications?unread=true", "/api/v1/jobs", "/api/v1/settings",
                 "/api/v1/system/status", "/api/v1/export", "/api/v1/export?format=csv&table=ledger"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
    body = client.get(f"/api/v1/proposals/{pr.id}").json()
    assert body["scope"]["paper_trading_only"] is True and body["rules_text"]["entry"]
    # Q&A cannot change rules
    qa = client.post(f"/api/v1/proposals/{pr.id}/qa", json={"question": "Can you change the exit rule?"}, headers=h).json()
    assert "cannot change" in qa["answer"].lower() or "cannot" in qa["note"].lower()
    # decision via API, idempotent
    key = uuid.uuid4().hex
    payload = {"decision": "yes", "idempotency_key": key, "expected_scope_hash": body["scope_hash"]}
    r1 = client.post(f"/api/v1/proposals/{pr.id}/decision", json=payload, headers=h)
    r2 = client.post(f"/api/v1/proposals/{pr.id}/decision", json=payload, headers=h)
    assert r1.status_code == 200 and r2.json()["idempotent_replay"] and r1.json()["experiment_id"] == r2.json()["experiment_id"]
    assert "awaiting" in r1.json()["message"].lower()
    eid = r1.json()["experiment_id"]
    assert client.get(f"/api/v1/experiments/{eid}").status_code == 200
    assert client.get(f"/api/v1/experiments/{eid}/trades").status_code == 200
    assert client.post(f"/api/v1/experiments/{eid}/close", json={"confirm": "no"}, headers=h).status_code == 422
    assert client.post(f"/api/v1/experiments/{eid}/close", json={"confirm": "CLOSE"}, headers=h).status_code == 200
    assert client.post(f"/api/v1/portfolios/{pid}/pause", json={"reason": "t"}, headers=h).json()["status"] == "paused"
    assert client.post(f"/api/v1/portfolios/{pid}/resume", headers=h).json()["status"] == "active"
    r = client.patch("/api/v1/settings", json={"min_score": 75, "risk_limits": {"max_ticker_fraction": 0.08}}, headers=h)
    assert r.status_code == 200 and r.json()["risk_policy_version"] == 2
    assert client.patch("/api/v1/settings", json={"risk_limits": {"leverage": 5}}, headers=h).status_code == 422
    assert client.post("/api/v1/admin/global-pause", json={"paused": True}, headers=h).json()["global_pause"] is True
    j = client.post("/api/v1/jobs/research", headers=h).json()
    j2 = client.post("/api/v1/jobs/research", headers=h).json()
    assert j["job_id"] == j2["job_id"] and not j2["created"]  # manual research deduplicated
    assert client.get(f"/api/v1/jobs/{j['job_id']}").status_code == 200
    assert client.post("/api/v1/admin/reset", json={"confirm": "nope"}, headers=h).status_code == 422


def test_tenant_isolation_over_http(client, db, user):
    from lab.bootstrap import hash_password
    from lab.services.portfolios import init_portfolios

    other = User(email="intruder@example.com", password_hash=hash_password("pw-intruder-1"))
    db.add(other)
    db.flush()
    init_portfolios(db, other.id)
    db.commit()
    pr = make_proposal(db, user)
    c2 = TestClient(app)
    h = login(c2, "intruder@example.com", "pw-intruder-1")
    assert c2.get(f"/api/v1/proposals/{pr.id}").status_code == 404
    assert c2.get(f"/api/v1/portfolios/{pr.portfolio_id}").status_code == 404
    r = c2.post(f"/api/v1/proposals/{pr.id}/decision", headers=h,
                json={"decision": "yes", "idempotency_key": uuid.uuid4().hex, "expected_scope_hash": pr.scope_hash})
    assert r.status_code == 404
    assert db.get(Proposal, pr.id).state == "pending"
    assert all(p["id"] != pr.portfolio_id for p in c2.get("/api/v1/portfolios").json()["leaderboard"])


def test_cron_endpoint_requires_secret(client):
    assert client.post("/internal/cron/tick").status_code == 403
    assert client.post("/internal/cron/tick", headers={"x-cron-secret": "wrong"}).status_code == 403
    assert client.post("/internal/cron/tick", headers={"x-cron-secret": "cron-test-secret"}).status_code == 200


@pytest.mark.slow
def test_demo_end_to_end_research_to_approval(db):
    """Full autonomous demo loop on the deterministic synthetic provider (no fixture shortcuts)."""
    from lab.agents.research import run_research
    from lab.bootstrap import bootstrap
    from lab.providers import market as market_mod
    from lab.services.engine import run_daily
    from lab.services.market_data import ingest

    market_mod.set_provider_override(None)  # real DemoMarketProvider
    uid = bootstrap()
    ing = ingest(db, years=5)
    db.commit()
    assert ing["status"] == "ok" and ing["inserted"] > 10000
    run = run_research(db, uid, use_llm=False)
    db.commit()
    props = db.scalars(select(Proposal).where(Proposal.state == "pending")).all()
    assert len(props) <= 3
    for p in props:
        assert p.card["synthetic"] is True and p.score >= 70 and all(v for k, v in p.gates.items() if k != "credible_validation")
    if props:
        from lab.services.proposals import decide

        r = decide(db, uid, props[0].id, "yes", uuid.uuid4().hex, props[0].scope_hash)
        db.commit()
        assert r["state"] == "approved"
        from lab import market_calendar as cal
        from datetime import datetime, timezone

        latest = cal.latest_finalized_session(datetime.now(timezone.utc))
        out = run_daily(db, uid, latest)
        db.commit()
        assert out["fills"] == 0  # approval happened after the latest close: nothing can fill retroactively
    assert run.briefing["data"]["synthetic"] is True
    _ = Decimal
