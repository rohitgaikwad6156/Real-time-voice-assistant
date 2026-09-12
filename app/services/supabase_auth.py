"""Supabase Auth configuration and server-side access-token verification."""

from __future__ import annotations

import os
from typing import Any, Dict

import httpx


class SupabaseAuthConfigError(RuntimeError):
    """Raised when the backend is missing its public Supabase configuration."""


def get_supabase_public_config() -> Dict[str, str]:
    """Return the browser-safe Supabase URL and publishable key."""
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    publishable_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
    if not url or not publishable_key:
        raise SupabaseAuthConfigError(
            "Supabase Auth is not configured. Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY."
        )
    if not (url.startswith("https://") or url.startswith("http://localhost") or url.startswith("http://127.0.0.1")):
        raise SupabaseAuthConfigError("SUPABASE_URL must use HTTPS outside local development.")
    return {"url": url, "publishable_key": publishable_key}


def verify_supabase_google_access_token(access_token: str) -> Dict[str, Any]:
    """Validate an access token with Supabase Auth and return its Google identity."""
    token = (access_token or "").strip()
    if not token:
        raise ValueError("Supabase access token is required.")

    config = get_supabase_public_config()
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{config['url']}/auth/v1/user",
                headers={
                    "apikey": config["publishable_key"],
                    "Authorization": f"Bearer {token}",
                },
            )
    except httpx.RequestError as exc:
        raise RuntimeError("Could not reach Supabase Auth.") from exc

    if response.status_code in {401, 403}:
        raise ValueError("Invalid or expired Supabase authentication token.")
    if response.status_code >= 400:
        raise RuntimeError("Supabase Auth could not verify the session.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Supabase Auth returned an invalid response.") from exc

    app_metadata = payload.get("app_metadata") if isinstance(payload.get("app_metadata"), dict) else {}
    providers = app_metadata.get("providers") if isinstance(app_metadata.get("providers"), list) else []
    provider = str(app_metadata.get("provider") or "")
    if provider != "google" and "google" not in providers:
        raise ValueError("This Supabase session was not authenticated with Google.")

    subject = str(payload.get("id") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    if not subject or not email:
        raise ValueError("Supabase user is missing required account information.")

    # user_metadata is used only for display fields, never authorization.
    user_metadata = payload.get("user_metadata") if isinstance(payload.get("user_metadata"), dict) else {}
    name = str(user_metadata.get("full_name") or user_metadata.get("name") or email.split("@", 1)[0]).strip()
    return {
        "sub": subject,
        "email": email,
        "name": name,
        "picture": user_metadata.get("avatar_url") or user_metadata.get("picture"),
    }
