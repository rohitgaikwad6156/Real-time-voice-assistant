"""Google OAuth ID-token verification for the voice assistant backend."""

from __future__ import annotations

import os
from typing import Any, Dict

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token


def get_google_client_id() -> str:
    """Return the configured public Google OAuth Web client ID."""
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def verify_google_credential(credential: str) -> Dict[str, Any]:
    """Verify a Google Identity Services credential and return a trusted profile."""
    client_id = get_google_client_id()
    if not client_id:
        raise RuntimeError("GOOGLE_CLIENT_ID is not configured in Render Environment.")

    token = (credential or "").strip()
    if not token:
        raise ValueError("Google credential is required.")

    try:
        payload = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            client_id,
        )
    except Exception as exc:
        raise ValueError("Google sign-in could not be verified.") from exc

    issuer = payload.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("Invalid Google token issuer.")

    if not payload.get("email_verified"):
        raise ValueError("Google account email is not verified.")

    email = str(payload.get("email") or "").strip().lower()
    subject = str(payload.get("sub") or "").strip()
    if not email or not subject:
        raise ValueError("Google token is missing required account information.")

    name = str(payload.get("name") or email.split("@", 1)[0]).strip()
    return {
        "sub": subject,
        "email": email,
        "name": name,
        "picture": payload.get("picture"),
    }
