import os
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from app.platform import Base, Job, LeaseLost, Platform, now
from app.main import create_app


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    url = os.getenv("TEST_DATABASE_URL") or "sqlite:///" + str(
        tmp_path / "production.db"
    )
    platform = Platform(url)
    if platform.engine.dialect.name == "postgresql":
        with platform.engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(platform.engine)
    platform.bootstrap("tenant-a", "admin@example.test", "Initial-Password-12345")
    platform.bootstrap("tenant-b", "other@example.test", "Initial-Password-12345")
    yield platform
    Base.metadata.drop_all(platform.engine)
    platform.engine.dispose()


def headers(client, email="admin@example.test"):
    response = client.post(
        "/api/auth/login", json={"email": email, "password": "Initial-Password-12345"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def add_user(client, admin, email, role, departments=None):
    response = client.post(
        "/api/users",
        headers=admin,
        json={
            "email": email,
            "name": email,
            "password": "Initial-Password-12345",
            "role": role,
            "departments": departments or [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_tenant_roles_and_revocation(environment):
    with TestClient(create_app(environment)) as client:
        assert client.get("/api/users").status_code == 401
        admin = headers(client)
        other = headers(client, "other@example.test")
        member = add_user(client, admin, "member@example.test", "member", ["ops"])
        restricted = headers(client, "member@example.test")
        assert client.get("/api/users", headers=restricted).status_code == 403
        assert len(client.get("/api/users", headers=admin).json()) == 2
        assert len(client.get("/api/users", headers=other).json()) == 1
        assert (
            client.put(
                "/api/users/" + member["id"],
                headers=other,
                json={"active": False, "role": "member", "departments": []},
            ).status_code
            == 404
        )
        assert (
            client.put(
                "/api/users/" + member["id"],
                headers=admin,
                json={"active": False, "role": "member", "departments": []},
            ).status_code
            == 200
        )
        assert client.get("/api/auth/me", headers=restricted).status_code == 401
        assert client.post("/api/auth/logout", headers=admin).status_code == 200
        assert client.get("/api/auth/me", headers=admin).status_code == 401


def test_login_throttle_and_no_sensitive_errors(environment):
    with TestClient(create_app(environment)) as client:
        for _ in range(8):
            response = client.post(
                "/api/auth/login",
                json={"email": "missing@example.test", "password": "bad"},
            )
            assert response.status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "missing@example.test", "password": "bad"},
            ).status_code
            == 429
        )


def test_leased_job_fencing_and_recovery(environment):
    with environment.transaction() as db:
        job = Job(tenant="tenant-a", owner_id="owner", kind="test", payload={})
        db.add(job)
        db.flush()
        job_id = job.id
    first = environment.claim()
    assert first.id == job_id and environment.claim() is None
    with environment.transaction() as db:
        db.get(Job, job_id).lease_until = now() - timedelta(seconds=1)
    second = environment.claim()
    assert (
        second.id == first.id
        and second.lease_token != first.lease_token
        and second.attempts == 2
    )
    with pytest.raises(LeaseLost):
        with environment.transaction() as db:
            environment.finish(db, first)
    with environment.transaction() as db:
        environment.finish(db, second)
    assert environment.claim() is None


def test_readiness_requires_worker(environment):
    with TestClient(create_app(environment)) as client:
        assert client.get("/api/ready").status_code == 503
        environment.heartbeat("test-worker")
        assert client.get("/api/ready").status_code == 200


def test_postgres_concurrent_claims_have_distinct_owners(environment):
    if environment.engine.dialect.name != "postgresql":
        pytest.skip(
            "Requires PostgreSQL row locks; SQLite is not evidence for queue concurrency"
        )
    from concurrent.futures import ThreadPoolExecutor

    with environment.transaction() as db:
        db.add_all(
            [
                Job(tenant="tenant-a", owner_id="owner", kind="test", payload={})
                for _ in range(8)
            ]
        )
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: environment.claim(), range(8)))
    assert len({job.id for job in claimed}) == 8
    assert all(job.attempts == 1 for job in claimed)
