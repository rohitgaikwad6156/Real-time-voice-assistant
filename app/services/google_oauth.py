"""Fast Google Identity Services ID-token verification with cached JWKS."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict

import jwt
import requests

logger = logging.getLogger(__name__)

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_cache_lock = threading.Lock()
_jwks_by_kid: Dict[str, Any] = {}
_jwks_expires_at = 0.0
_warmup_started = False


def get_google_client_id() -> str:
    """Return the configured public Google OAuth Web client ID."""
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def _cache_ttl_seconds(cache_control: str) -> int:
    match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control or "", re.IGNORECASE)
    if not match:
        return 3600
    return max(60, min(int(match.group(1)), 24 * 60 * 60))


def _refresh_google_jwks(force: bool = False) -> Dict[str, Any]:
    """Fetch and cache Google's public signing keys."""
    global _jwks_by_kid, _jwks_expires_at

    with _cache_lock:
        now = time.time()
        if not force and _jwks_by_kid and now < _jwks_expires_at:
            return _jwks_by_kid

        response = requests.get(GOOGLE_JWKS_URL, timeout=5)
        response.raise_for_status()
        payload = response.json()
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list) or not keys:
            raise RuntimeError("Google verification keys are unavailable.")

        parsed: Dict[str, Any] = {}
        for item in keys:
            if not isinstance(item, dict) or not item.get("kid"):
                continue
            try:
                parsed[str(item["kid"])] = jwt.PyJWK.from_dict(item).key
            except Exception:
                continue

        if not parsed:
            raise RuntimeError("Google verification keys could not be loaded.")

        ttl = _cache_ttl_seconds(response.headers.get("Cache-Control", ""))
        _jwks_by_kid = parsed
        _jwks_expires_at = now + ttl
        return _jwks_by_kid


def _warm_google_jwks_worker() -> None:
    global _warmup_started
    try:
        _refresh_google_jwks()
    except Exception as exc:
        logger.warning("Google JWKS warmup failed: %s", type(exc).__name__)
    finally:
        with _cache_lock:
            _warmup_started = False


def start_google_verification_warmup() -> None:
    """Warm Google's signing keys while the user is choosing an account."""
    global _warmup_started

    if not get_google_client_id():
        return

    with _cache_lock:
        if _jwks_by_kid and time.time() < _jwks_expires_at:
            return
        if _warmup_started:
            return
        _warmup_started = True

    threading.Thread(
        target=_warm_google_jwks_worker,
        name="google-jwks-warmup",
        daemon=True,
    ).start()


def verify_google_credential(credential: str) -> Dict[str, Any]:
    """Verify a Google Identity Services credential and return a trusted profile."""
    client_id = get_google_client_id()
    if not client_id:
        raise RuntimeError("GOOGLE_CLIENT_ID is not configured in Render Environment.")

    token = (credential or "").strip()
    if not token:
        raise ValueError("Google credential is required.")

    try:
        header = jwt.get_unverified_header(token)
        kid = str(header.get("kid") or "").strip()
        if not kid:
            raise ValueError("Google token is missing a signing key id.")

        keys = _refresh_google_jwks()
        key = keys.get(kid)
        if key is None:
            keys = _refresh_google_jwks(force=True)
            key = keys.get(kid)
        if key is None:
            raise ValueError("Google signing key was not found.")

        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            options={
                "require": ["exp", "iat", "aud", "sub"],
                "verify_iss": False,
            },
        )
    except (jwt.PyJWTError, requests.RequestException, RuntimeError, ValueError) as exc:
        raise ValueError("Google sign-in could not be verified.") from exc

    issuer = payload.get("iss")
    if issuer not in _VALID_ISSUERS:
        raise ValueError("Invalid Google token issuer.")

    if payload.get("email_verified") is not True:
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
