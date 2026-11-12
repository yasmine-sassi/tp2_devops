from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings, load_settings
from app.main import create_app
from app.security import ALGORITHM


def test_health_returns_ok_and_version(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
    assert "X-Response-Time-Ms" in response.headers


def test_token_issued_for_valid_credentials(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/auth/token",
        json={"username": settings.demo_user, "password": settings.demo_password},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.token_ttl_seconds
    claims = jwt.decode(body["access_token"], settings.jwt_secret, algorithms=[ALGORITHM])
    assert claims["sub"] == settings.demo_user


@pytest.mark.parametrize(
    ("username", "password_suffix"),
    [("demo", "-wrong"), ("intruder", "")],
)
def test_token_rejected_for_bad_credentials(
    client: TestClient, settings: Settings, username: str, password_suffix: str
) -> None:
    response = client.post(
        "/auth/token",
        json={"username": username, "password": f"{settings.demo_password}{password_suffix}"},
    )
    assert response.status_code == 401


def test_token_refused_when_no_password_configured(settings: Settings) -> None:
    locked = Settings(**{**settings.__dict__, "demo_password": None})
    client = TestClient(create_app(locked))
    response = client.post("/auth/token", json={"username": "demo", "password": "anything"})
    assert response.status_code == 401


def test_token_request_validation(client: TestClient) -> None:
    assert client.post("/auth/token", json={"username": "demo"}).status_code == 422


def test_protected_route_requires_token(client: TestClient) -> None:
    response = client.get("/accounts")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_protected_route_rejects_garbage_token(client: TestClient) -> None:
    response = client.get("/accounts", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_protected_route_rejects_expired_token(client: TestClient, settings: Settings) -> None:
    past = int(time.time()) - 3600
    token = jwt.encode(
        {"sub": "demo", "iat": past, "exp": past + 1}, settings.jwt_secret, algorithm=ALGORITHM
    )
    response = client.get("/accounts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_load_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYFLEX_DEMO_USER", "ops")
    monkeypatch.setenv("PAYFLEX_DEMO_PASSWORD", "from-env")
    monkeypatch.setenv("PAYFLEX_TOKEN_TTL", "60")
    monkeypatch.setenv("SIMULATED_LATENCY_MS", "-5")
    monkeypatch.delenv("PAYFLEX_JWT_SECRET", raising=False)
    loaded = load_settings()
    assert loaded.demo_user == "ops"
    assert loaded.demo_password == "from-env"
    assert loaded.token_ttl_seconds == 60
    assert loaded.simulated_latency_ms == 0
    assert len(loaded.jwt_secret) >= 32


def test_simulated_latency_slows_business_routes_only(settings: Settings) -> None:
    slow = TestClient(create_app(Settings(**{**settings.__dict__, "simulated_latency_ms": 50})))
    start = time.perf_counter()
    slow.get("/accounts")
    assert (time.perf_counter() - start) * 1000 >= 50
    assert slow.get("/health").status_code == 200
