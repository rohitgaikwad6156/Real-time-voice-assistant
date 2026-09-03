import logging
import os
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from app.services.session_manager import handle_voice_websocket
from app.services.voice_pipeline import answer_from_text, transcribe_audio, generate_speech

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="AI Voice Assistant", version="1.0.0")

# Cross-Origin Resource Sharing (CORS) Configuration
# Permits local development, live Render backend, and any Vercel deployment (*.vercel.app)
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

class TextRequest(BaseModel):
    text: str

@app.get("/")
def home():
    return FileResponse(
        BASE_DIR / "static" / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )

@app.get("/health")
def health():
    return {"status": "ok"}

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
