"""MongoDB persistence for authenticated multi-user voice assistant data."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database


class DatabaseConfigError(RuntimeError):
    pass


_client: Optional[MongoClient] = None
_db: Optional[Database] = None
_indexes_ready = False


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

    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        raise DatabaseConfigError(
            "MONGODB_URI is not configured. Add your MongoDB Atlas connection string in Render Environment."
        )

    db_name = os.getenv("MONGODB_DB_NAME", "voice_assistant").strip() or "voice_assistant"
    _client = MongoClient(uri, serverSelectionTimeoutMS=7000, connectTimeoutMS=7000)
    _client.admin.command("ping")
    _db = _client[db_name]

    if not _indexes_ready:
        _db.users.create_index([("email", ASCENDING)], unique=True)
        _db.conversations.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
        _db.messages.create_index([("conversation_id", ASCENDING), ("created_at", ASCENDING)])
        _db.messages.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        _db.reminders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        _indexes_ready = True

    return _db


def database_status() -> Dict[str, Any]:
    configured = bool(os.getenv("MONGODB_URI", "").strip())
    if not configured:
        return {"configured": False, "connected": False}
    try:
        get_database()
        return {"configured": True, "connected": True}
    except Exception as exc:
        return {"configured": True, "connected": False, "error": str(exc)}


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


def _maybe_update_conversation_title(user_id: str, conversation_id: str, text: str) -> None:
    db = get_database()
    try:
        oid = _oid(conversation_id)
    except ValueError:
        return
    conversation = db.conversations.find_one({"_id": oid, "user_id": user_id})
    if not conversation:
        return
    update: Dict[str, Any] = {"updated_at": _now()}
    if conversation.get("title") in (None, "", "New conversation"):
        clean = " ".join(text.strip().split())
        if clean:
            update["title"] = clean[:60] + ("…" if len(clean) > 60 else "")
    db.conversations.update_one({"_id": oid, "user_id": user_id}, {"$set": update})


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
    _maybe_update_conversation_title(user_id, conversation_id, clean if role == "user" else "")
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
