from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret=secrets.token_urlsafe(32),
        token_ttl_seconds=900,
        demo_user="demo",
        demo_password=secrets.token_urlsafe(16),
        simulated_latency_ms=0,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def auth_headers(client: TestClient, settings: Settings) -> dict[str, str]:
    response = client.post(
        "/auth/token",
        json={"username": settings.demo_user, "password": settings.demo_password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def account(client: TestClient, auth_headers: dict[str, str]) -> dict:
    response = client.post(
        "/accounts",
        json={"holder_name": "Alice Martin", "currency": "EUR", "initial_balance": "1000.00"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()
