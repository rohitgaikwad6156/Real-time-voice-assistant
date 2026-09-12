import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
from pymongo.errors import DuplicateKeyError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from app.database.mongodb import (
    DatabaseConfigError,
    create_conversation,
    create_user,
    create_user_reminder,
    database_status,
    delete_conversation_messages,
    get_messages,
    get_user_by_email,
    get_user_by_id,
    list_conversations,
    list_user_reminders,
)
from app.services.auth import create_access_token, decode_access_token, hash_password, verify_password
from app.services.google_oauth import get_google_client_id, verify_google_credential
from app.services.session_manager import handle_voice_websocket
from app.services.voice_pipeline import answer_from_text, transcribe_audio, generate_speech

app = FastAPI(title="AI Voice Assistant Backend", version="2.1.0")

ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://real-time-voice-assistant-9bh1.onrender.com",
]

custom_frontend = os.getenv("FRONTEND_URL")
if custom_frontend and custom_frontend not in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS.append(custom_frontend.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^https://.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


class TextRequest(BaseModel):
    text: str


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str


class ConversationRequest(BaseModel):
    title: Optional[str] = "New conversation"


class ReminderRequest(BaseModel):
    title: str
    remind_at: str


def current_user(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
        user = get_user_by_id(str(payload["sub"]))
    except (ValueError, RuntimeError, DatabaseConfigError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=401, detail="User account not found.")
    return user


@app.get("/")
def home():
    return {
        "status": "ok",
        "service": "Real-Time Voice Assistant Backend",
        "frontend": custom_frontend or "Vercel frontend",
        "health": "/health",
        "ready": "/ready",
        "api": "/api",
        "websocket": "/ws/voice",
    }


@app.get("/health")
def health():
    # Keep this endpoint intentionally lightweight so Render cold-start polling
    # does not also block on the first MongoDB connection.
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"status": "ok", "database": database_status()}


@app.post("/api/auth/register")
def register(request: RegisterRequest):
    name = request.name.strip()
    password = request.password
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Name must contain at least 2 characters.")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must contain at least 6 characters.")
    try:
        if get_user_by_email(str(request.email)):
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        user = create_user(name, str(request.email), hash_password(password))
        token = create_access_token(user["id"], user["email"])
        return {"access_token": token, "token_type": "bearer", "user": user}
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from exc
    except DatabaseConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/auth/login")
def login(request: LoginRequest):
    try:
        user = get_user_by_email(str(request.email), include_password=True)
    except DatabaseConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    password_hash = user.get("password_hash", "") if user else ""
    if not user or not password_hash or not verify_password(request.password, password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    public_user = {k: v for k, v in user.items() if k != "password_hash"}
    token = create_access_token(public_user["id"], public_user["email"])
    return {"access_token": token, "token_type": "bearer", "user": public_user}


@app.get("/api/auth/google/config")
def google_auth_config():
    client_id = get_google_client_id()
    return {
        "enabled": bool(client_id),
        "client_id": client_id or None,
    }


@app.post("/api/auth/google")
def google_login(request: GoogleAuthRequest):
    try:
        profile = verify_google_credential(request.credential)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        user = get_user_by_email(profile["email"])
        if not user:
            try:
                # Google users do not need a usable password. Store an unknown,
                # random bcrypt hash so password login cannot be used accidentally.
                random_password = secrets.token_urlsafe(48)
                user = create_user(
                    profile["name"],
                    profile["email"],
                    hash_password(random_password),
                )
            except DuplicateKeyError:
                # Another request may have created the same verified email first.
                user = get_user_by_email(profile["email"])

        if not user:
            raise HTTPException(status_code=500, detail="Could not create Google account.")

        token = create_access_token(user["id"], user["email"])
        return {"access_token": token, "token_type": "bearer", "user": user}
    except DatabaseConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return {"user": user}


@app.get("/api/conversations")
def conversations(user=Depends(current_user)):
    return {"conversations": list_conversations(user["id"])}


@app.post("/api/conversations")
def new_conversation(request: ConversationRequest, user=Depends(current_user)):
    return {"conversation": create_conversation(user["id"], request.title or "New conversation")}


@app.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str, user=Depends(current_user)):
    return {"messages": get_messages(user["id"], conversation_id)}


@app.delete("/api/conversations/{conversation_id}/messages")
def clear_conversation_messages(conversation_id: str, user=Depends(current_user)):
    deleted_count = delete_conversation_messages(user["id"], conversation_id)
    if deleted_count is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {
        "status": "ok",
        "conversation_id": conversation_id,
        "deleted_count": deleted_count,
    }


@app.get("/api/reminders")
def reminders(user=Depends(current_user)):
    return {"reminders": list_user_reminders(user["id"])}


@app.post("/api/reminders")
def add_reminder(request: ReminderRequest, user=Depends(current_user)):
    if not request.title.strip() or not request.remind_at.strip():
        raise HTTPException(status_code=400, detail="Title and reminder time are required.")
    return {"reminder": create_user_reminder(user["id"], request.title, request.remind_at)}


@app.websocket("/ws/voice")
async def voice_websocket_endpoint(websocket: WebSocket):
    await handle_voice_websocket(websocket)


@app.post("/api/text")
def text_pipeline(request: TextRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Please enter some text.")
    try:
        answer = answer_from_text(text)
        audio = generate_speech(answer)
        return {"transcript": text, "answer": answer, "audio_url": f"/api/audio/{audio.name}"}
    except RuntimeError as err:
        raise HTTPException(status_code=503, detail=str(err))


@app.post("/api/voice")
async def voice_pipeline(audio: UploadFile = File(...)):
    if not audio.filename:
        raise HTTPException(status_code=400, detail="Audio file is required.")
    suffix = Path(audio.filename).suffix or ".webm"
    temp = Path("data") / f"input{suffix}"
    temp.parent.mkdir(exist_ok=True)
    try:
        temp.write_bytes(await audio.read())
        transcript = transcribe_audio(temp)
        answer = answer_from_text(transcript)
        output = generate_speech(answer)
        return {"transcript": transcript, "answer": answer, "audio_url": f"/api/audio/{output.name}"}
    except RuntimeError as err:
        raise HTTPException(status_code=503, detail=str(err))
    finally:
        temp.unlink(missing_ok=True)


@app.get("/api/audio/{filename}")
def get_audio(filename: str):
    path = Path("data/audio") / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found.")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)
