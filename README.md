# Real-Time Voice Assistant

A full-stack voice assistant built with FastAPI, Google Gemini Live, MongoDB, and the Web Audio API. The browser streams 16 kHz PCM microphone audio through an authenticated WebSocket and plays Gemini's streamed 24 kHz response.

## Deployments

- Frontend: <https://real-time-voice-assistant-lovat.vercel.app>
- Backend: <https://real-time-voice-assistant-9bh1.onrender.com>
- Health: <https://real-time-voice-assistant-9bh1.onrender.com/health>

## Features

- Real-time, bidirectional speech with interruption support
- Text input fallback over the same Gemini Live session
- Email/password authentication plus Google OAuth through Supabase Auth
- Per-user conversation history, reminders, and notes in MongoDB
- Weather function calling through Open-Meteo
- Animated voice state, live waveform, transcripts, and tool activity
- JWT authentication sent as the first WebSocket message, never in its URL
- Bounded audio uploads and process-local rate limiting for costly/public entry points

## Architecture

```text
Vercel /public frontend
  -> local email/password or Supabase Google OAuth
  -> Supabase access-token exchange for an application JWT
  -> authenticated history REST requests
  -> authenticated WebSocket (/ws/voice)
      -> FastAPI VoiceSession
          -> Gemini Live API
          -> weather tool (Open-Meteo)
          -> reminders and notes (MongoDB, scoped by user id)
      <- transcript, audio, tool, and lifecycle events
```

MongoDB stores users, conversations, messages, reminders, and authenticated notes. The older SQLite module remains only as a compatibility fallback for isolated unit tests and direct local tool calls without a user id. The deprecated OpenAI REST pipeline remains available to authenticated callers at `/api/text` and `/api/voice`.

## Project layout

```text
app/
  main.py                     FastAPI routes, validation, CORS
  database/
    mongodb.py                Authenticated user data
    database.py               Legacy SQLite test/local fallback
  services/
    auth.py                   JWT and password helpers
    gemini_client.py          Gemini Live SDK wrapper
    supabase_auth.py          Supabase session verification
    rate_limiter.py           Process-local sliding-window limiter
    session_manager.py        WebSocket lifecycle and persistence
    tool_executor.py          Concurrent function dispatch
    voice_pipeline.py         Deprecated authenticated OpenAI REST flow
  tools/                      Weather, reminder, and note tools
public/                       Canonical Vercel frontend
tests/                        Unit, reliability, and opt-in integration tests
render.yaml                   Render Blueprint
vercel.json                   Vercel static deployment
```

## Local setup

Requires Python 3.11 or newer, MongoDB, and a Gemini API key.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 10000
```

Serve the `public/` directory from a local static server. Local ports 8000, 10000, 3000, and 5173 are allowed by the backend CORS configuration.

## Configuration

Keep all secrets in `.env` locally and in Render environment variables in production.

| Variable | Purpose | Required |
|---|---|---|
| `GEMINI_API_KEY` | Gemini Live access | Yes |
| `GEMINI_LIVE_MODEL` | Live model override | No |
| `GEMINI_VOICE_NAME` | Spoken voice | No |
| `MONGODB_URI` | Users, history, reminders, notes | Yes |
| `MONGODB_DB_NAME` | MongoDB database name | No |
| `JWT_SECRET` | Signs access tokens | Yes |
| `JWT_EXPIRE_MINUTES` | Token lifetime | No |
| `SUPABASE_URL` | Browser-safe Supabase project URL | For Google login |
| `SUPABASE_PUBLISHABLE_KEY` | Browser-safe publishable key; never use a secret/service-role key | For Google login |
| `FRONTEND_URL` | Additional exact CORS origin | In production |
| `WEATHER_API_KEY` | Use `open-meteo` for the configured provider | Yes |
| `OPENAI_API_KEY` | Deprecated REST text/voice endpoints | Optional |

`render.yaml` declares the production variables. Values marked `sync: false` must be entered in Render and are never committed.

## API overview

Public endpoints:

- `GET /health` — lightweight liveness check
- `GET /ready` — sanitized database readiness
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/supabase/config`
- `POST /api/auth/supabase` — exchanges a verified Supabase Google session for an application JWT

Bearer-authenticated endpoints:

- `GET /api/auth/me`
- `GET|POST /api/conversations`
- `GET /api/conversations/{id}/messages`
- `DELETE /api/conversations/{id}/messages`
- `GET|POST /api/reminders`
- `POST /api/text` and `POST /api/voice` — deprecated OpenAI fallback
- `GET /api/audio/{filename}`

For `/ws/voice`, connect without credentials in the URL, wait for `connected`, then send:

```json
{
  "type": "auth",
  "token": "<JWT>",
  "conversation_id": "<MongoDB conversation id>"
}
```

Do not send audio or prompts until the server returns `authenticated`.

## Tests

Run deterministic tests without external services:

```powershell
pytest -m "not integration"
```

Run opt-in live API tests only with valid test credentials and isolated data:

```powershell
pytest -m integration
```

The normal suite mocks network failures and redirects legacy SQLite writes to temporary databases. `.env`, `*.db`, virtual environments, caches, and Vercel local state are ignored by Git.

## Production notes

- The included rate limiter is process-local. Use a shared gateway or Redis-backed limiter when scaling to multiple workers or instances.
- WebSocket JWTs are carried in the first message to avoid leaking them through URL logs.
- Only explicit frontend origins receive CORS access; add preview origins deliberately through configuration instead of accepting every `*.vercel.app` domain.
- Notes created before the MongoDB migration are not automatically copied from the legacy SQLite database.

## Supabase Google setup

1. In Supabase Dashboard, open **Authentication > Providers > Google**, enable it, and enter the Google OAuth client ID and client secret.
2. In Google Auth Platform, register the callback URL shown by Supabase. Hosted projects use `https://<project-ref>.supabase.co/auth/v1/callback`.
3. In Supabase **Authentication > URL Configuration**, set the Site URL to the Vercel frontend and add any deliberate local/preview redirect URLs.
4. Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` in Render. The publishable key is safe for the browser; never configure or expose a Supabase secret/service-role key.

The browser uses the pinned `@supabase/supabase-js` 2.116.0 bundle and `signInWithOAuth({ provider: "google" })`. After the OAuth redirect, the backend validates the access token against Supabase Auth before linking the verified email to the existing MongoDB user record.
