"""MongoDB persistence for authenticated multi-user voice assistant data."""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database


logger = logging.getLogger(__name__)


class DatabaseConfigError(RuntimeError):
    pass


_client: Optional[MongoClient] = None
_db: Optional[Database] = None
_indexes_ready = False
_db_lock = threading.Lock()
_warmup_started = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    for key, value in list(out.items()):
        if isinstance(value, ObjectId):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    out.pop("password_hash", None)
    return out


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise ValueError("Invalid resource id.") from exc


def get_database() -> Database:
    global _client, _db, _indexes_ready
    if _db is not None:
        return _db

    with _db_lock:
        if _db is not None:
            return _db

        uri = os.getenv("MONGODB_URI", "").strip()
        if not uri:
            raise DatabaseConfigError(
                "MONGODB_URI is not configured. Add your MongoDB Atlas connection string in Render Environment."
            )

        db_name = os.getenv("MONGODB_DB_NAME", "voice_assistant").strip() or "voice_assistant"
        _client = MongoClient(
            uri,
            serverSelectionTimeoutMS=7000,
            connectTimeoutMS=7000,
            appName="real-time-voice-assistant",
        )
        _client.admin.command("ping")
        _db = _client[db_name]

        if not _indexes_ready:
            _db.users.create_index([("email", ASCENDING)], unique=True)
            _db.conversations.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
            _db.messages.create_index([("conversation_id", ASCENDING), ("created_at", ASCENDING)])
            _db.messages.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
            _db.reminders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
            _db.notes.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
            _indexes_ready = True

        return _db


def _database_warmup_worker() -> None:
    global _warmup_started
    try:
        get_database()
        logger.info("MongoDB warmup complete.")
    except Exception as exc:
        logger.warning("MongoDB warmup failed: %s", type(exc).__name__)
    finally:
        _warmup_started = False


def start_database_warmup() -> None:
    """Warm MongoDB in the background without blocking server startup."""
    global _warmup_started
    if _db is not None or _warmup_started or not os.getenv("MONGODB_URI", "").strip():
        return
    _warmup_started = True
    threading.Thread(
        target=_database_warmup_worker,
        name="mongodb-warmup",
        daemon=True,
    ).start()


def database_status() -> Dict[str, Any]:
    configured = bool(os.getenv("MONGODB_URI", "").strip())
    if not configured:
        return {"configured": False, "connected": False}
    try:
        get_database()
        return {"configured": True, "connected": True}
    except Exception as exc:
        logger.warning("MongoDB readiness check failed: %s", type(exc).__name__)
        return {"configured": True, "connected": False, "error": "Database connection unavailable."}


# ----------------------------- Users ---------------------------------

def create_user(name: str, email: str, password_hash: str) -> Dict[str, Any]:
    db = get_database()
    now = _now()
    doc = {
        "name": name.strip(),
        "email": email.strip().lower(),
        "password_hash": password_hash,
        "created_at": now,
        "updated_at": now,
    }
    result = db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc) or {}


def get_user_by_email(email: str, include_password: bool = False) -> Optional[Dict[str, Any]]:
    db = get_database()
    doc = db.users.find_one({"email": email.strip().lower()})
    if doc is None:
        return None
    if include_password:
        out = dict(doc)
        out["id"] = str(out.pop("_id"))
        return out
    return _serialize(doc)


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    db = get_database()
    return _serialize(db.users.find_one({"_id": _oid(user_id)}))


# -------------------------- Conversations -----------------------------

def create_conversation(user_id: str, title: str = "New conversation") -> Dict[str, Any]:
    db = get_database()
    now = _now()
    doc = {
        "user_id": user_id,
        "title": (title or "New conversation").strip()[:80],
        "ai_title_generated": False,
        "created_at": now,
        "updated_at": now,
    }
    result = db.conversations.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc) or {}


def get_conversation(user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
    db = get_database()
    try:
        oid = _oid(conversation_id)
    except ValueError:
        return None
    return _serialize(db.conversations.find_one({"_id": oid, "user_id": user_id}))


def ensure_conversation(user_id: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
    if conversation_id:
        existing = get_conversation(user_id, conversation_id)
        if existing:
            return existing
    return create_conversation(user_id)


def list_conversations(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    db = get_database()
    cursor = db.conversations.find({"user_id": user_id}).sort("updated_at", DESCENDING).limit(max(1, min(limit, 100)))
    return [_serialize(doc) or {} for doc in cursor]


def _derive_ai_title(text: str) -> str:
    """Create a short history title from the assistant's own first response."""
    clean = re.sub(r"[*_`#>]+", " ", text or "")
    clean = " ".join(clean.split()).strip()
    clean = re.sub(
        r"^(sure|of course|absolutely|certainly|okay|ok|here(?:'s| is))[,!:\-\s]+",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    if not clean:
        return "New conversation"

    first_sentence = re.split(r"[.!?]", clean, maxsplit=1)[0].strip(" \t\n\r:;-")
    words = first_sentence.split()
    if len(words) > 8:
        first_sentence = " ".join(words[:8])

    title = first_sentence[:60].strip(" \t\n\r:;,-")
    return title or "New conversation"


def _maybe_update_conversation_metadata(
    user_id: str,
    conversation_id: str,
    role: str,
    text: str,
) -> None:
    """Update activity time and create the title from the first AI response."""
    db = get_database()
    try:
        oid = _oid(conversation_id)
    except ValueError:
        return

    conversation = db.conversations.find_one({"_id": oid, "user_id": user_id})
    if not conversation:
        return

    update: Dict[str, Any] = {"updated_at": _now()}

    if role == "assistant" and not conversation.get("ai_title_generated", False):
        update["title"] = _derive_ai_title(text)
        update["ai_title_generated"] = True
        update.pop("description", None)

    db.conversations.update_one(
        {"_id": oid, "user_id": user_id},
        {
            "$set": update,
            "$unset": {"description": ""},
        },
    )


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    """Delete an owned conversation and all of its messages."""
    db = get_database()
    try:
        oid = _oid(conversation_id)
    except ValueError:
        return False

    conversation = db.conversations.find_one({"_id": oid, "user_id": user_id})
    if not conversation:
        return False

    db.messages.delete_many({
        "user_id": user_id,
        "conversation_id": conversation_id,
    })
    result = db.conversations.delete_one({"_id": oid, "user_id": user_id})
    return result.deleted_count == 1


# ----------------------------- Messages -------------------------------

def save_message(user_id: str, conversation_id: str, role: str, text: str) -> Optional[Dict[str, Any]]:
    clean = (text or "").strip()
    if not clean:
        return None
    db = get_database()
    conversation = get_conversation(user_id, conversation_id)
    if not conversation:
        return None
    doc = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "role": role,
        "text": clean,
        "created_at": _now(),
    }
    result = db.messages.insert_one(doc)
    doc["_id"] = result.inserted_id
    _maybe_update_conversation_metadata(user_id, conversation_id, role, clean)
    return _serialize(doc)


def get_messages(user_id: str, conversation_id: str, limit: int = 300) -> List[Dict[str, Any]]:
    db = get_database()
    if not get_conversation(user_id, conversation_id):
        return []
    cursor = (
        db.messages.find({"user_id": user_id, "conversation_id": conversation_id})
        .sort("created_at", ASCENDING)
        .limit(max(1, min(limit, 1000)))
    )
    return [_serialize(doc) or {} for doc in cursor]


def delete_conversation_messages(user_id: str, conversation_id: str) -> Optional[int]:
    """Delete messages only when the conversation belongs to this authenticated user."""
    db = get_database()
    conversation = get_conversation(user_id, conversation_id)
    if not conversation:
        return None

    result = db.messages.delete_many({
        "user_id": user_id,
        "conversation_id": conversation_id,
    })
    return int(result.deleted_count)


# ----------------------------- Reminders ------------------------------

def create_user_reminder(user_id: str, title: str, remind_at: str) -> Dict[str, Any]:
    db = get_database()
    now = _now()
    doc = {
        "user_id": user_id,
        "title": title.strip(),
        "remind_at": remind_at.strip(),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    result = db.reminders.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc) or {}


def list_user_reminders(user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    db = get_database()
    cursor = db.reminders.find({"user_id": user_id}).sort("created_at", DESCENDING).limit(max(1, min(limit, 200)))
    return [_serialize(doc) or {} for doc in cursor]


# ------------------------------- Notes --------------------------------

def search_user_notes(user_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Search only notes owned by ``user_id`` using a case-insensitive literal match."""
    db = get_database()
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    pattern = re.escape(clean_query)
    cursor = (
        db.notes.find({
            "user_id": user_id,
            "$or": [
                {"title": {"$regex": pattern, "$options": "i"}},
                {"content": {"$regex": pattern, "$options": "i"}},
            ],
        })
        .sort("updated_at", DESCENDING)
        .limit(max(1, min(limit, 20)))
    )
    return [_serialize(doc) or {} for doc in cursor]
