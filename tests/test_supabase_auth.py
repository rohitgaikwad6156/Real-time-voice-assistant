"""Tests for Supabase Google session verification and public configuration."""

import importlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.supabase_auth import (
    SupabaseAuthConfigError,
    get_supabase_public_config,
    verify_supabase_google_access_token,
)


main_module = importlib.import_module("app.main")


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers):
        self.request = {"url": url, "headers": headers}
        return self.response


def configure(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")


def test_public_config_requires_both_values(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    with pytest.raises(SupabaseAuthConfigError):
        get_supabase_public_config()


def test_public_config_endpoint_is_disabled_when_unconfigured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    response = TestClient(app).get("/api/auth/supabase/config")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "url": None, "publishable_key": None}


def test_public_config_endpoint_returns_browser_safe_values(monkeypatch):
    configure(monkeypatch)
    response = TestClient(app).get("/api/auth/supabase/config")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "url": "https://project-ref.supabase.co",
        "publishable_key": "sb_publishable_test",
    }


def test_verifies_google_user_through_supabase(monkeypatch):
    configure(monkeypatch)
    fake = FakeClient(FakeResponse(payload={
        "id": "supabase-user-id",
        "email": "Person@Example.com",
        "app_metadata": {"provider": "google", "providers": ["google"]},
        "user_metadata": {"full_name": "Test Person", "avatar_url": "https://example.com/avatar.png"},
    }))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake)

    profile = verify_supabase_google_access_token("access-token")

    assert profile["sub"] == "supabase-user-id"
    assert profile["email"] == "person@example.com"
    assert profile["name"] == "Test Person"
    assert fake.request["url"] == "https://project-ref.supabase.co/auth/v1/user"
    assert fake.request["headers"]["apikey"] == "sb_publishable_test"
    assert fake.request["headers"]["Authorization"] == "Bearer access-token"


def test_rejects_non_google_supabase_session(monkeypatch):
    configure(monkeypatch)
    fake = FakeClient(FakeResponse(payload={
        "id": "supabase-user-id",
        "email": "person@example.com",
        "app_metadata": {"provider": "email", "providers": ["email"]},
    }))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake)
    with pytest.raises(ValueError, match="not authenticated with Google"):
        verify_supabase_google_access_token("access-token")


def test_rejects_expired_supabase_session(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: FakeClient(FakeResponse(status_code=401)))
    with pytest.raises(ValueError, match="Invalid or expired"):
        verify_supabase_google_access_token("expired-token")


def test_exchange_endpoint_links_verified_supabase_user(monkeypatch):
    profile = {"sub": "supabase-id", "email": "person@example.com", "name": "Test Person"}
    user = {"id": "mongo-user-id", "email": profile["email"], "name": profile["name"]}
    monkeypatch.setattr(main_module, "verify_supabase_google_access_token", lambda token: profile)
    monkeypatch.setattr(main_module, "get_user_by_email", lambda email: user)
    monkeypatch.setattr(main_module, "create_access_token", lambda user_id, email: "application-jwt")

    response = TestClient(app).post(
        "/api/auth/supabase",
        json={"access_token": "verified-by-mock"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "application-jwt",
        "token_type": "bearer",
        "user": user,
    }
