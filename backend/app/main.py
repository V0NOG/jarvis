# backend/app/main.py
import os, json
from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse
from .speech import detect_wake_and_record, transcribe, heard_wake
from .brain import chat_and_route
from .tts import speak
from . import actions as act
import asyncio, time, traceback

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or lock to http://localhost:5173 etc.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NEW: env knobs
LOCAL_ONLY = os.getenv("LOCAL_ONLY", "0") == "1"        # skip LLM entirely
LLM_COOLDOWN_SEC = float(os.getenv("LLM_COOLDOWN_SEC", "45"))  # cooldown after 429
IDLE_LOOP_SLEEP = float(os.getenv("IDLE_LOOP_SLEEP", "0.15"))  # loop sleep while idle/suppressed

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/listen-once")
async def listen_once():
    wav = await asyncio.to_thread(detect_wake_and_record, max_silence_ms=800)
    text = await asyncio.to_thread(transcribe, wav)
    return {"transcript": text, "wake": heard_wake(text)}

@app.get("/events")
async def events():
    DEBUG_EVENTS = os.getenv("DEBUG_EVENTS", "0") == "1"

    async def gen():
        yield {"event": "status", "data": "idle"}

        suppress_until = 0.0
        last_ping = time.monotonic()

        while True:
            now = time.monotonic()

            if now - last_ping > 15:
                yield {"event": "ping", "data": f"{time.strftime('%Y-%m-%d %H:%M:%S')}"}
                last_ping = now

            if now < suppress_until:
                await asyncio.sleep(0.1)
                continue

            yield {"event": "status", "data": "listening"}
            try:
                wav = await asyncio.to_thread(detect_wake_and_record)
            except Exception as e:
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": f"detect_error: {type(e).__name__}: {e}"}
                await asyncio.sleep(0.3)
                yield {"event": "status", "data": "idle"}
                continue

            yield {"event": "status", "data": "processing"}
            try:
                text = await asyncio.to_thread(transcribe, wav)
            except Exception as e:
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": f"transcribe_error: {type(e).__name__}: {e}"}
                await asyncio.sleep(0.3)
                yield {"event": "status", "data": "idle"}
                continue

            wake = heard_wake(text)
            # send transcript as JSON with wake flag so UI can decide what to show
            payload = json.dumps({"text": text or "", "wake": wake})
            yield {"event": "transcript", "data": payload}

            if not wake:
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": "no_wake -> idle"}
                yield {"event": "status", "data": "idle"}
                continue

            # Local actions first
            try:
                a = act.maybe_handle(text)
            except Exception as e:
                a = None
                yield {"event": "error", "data": f"action_router_error: {type(e).__name__}: {e}"}

            if a and a.get("reply"):
                yield {"event": "action", "data": str(a)}
                try:
                    speak(a["reply"])
                except Exception as e:
                    yield {"event": "error", "data": f"tts_error: {type(e).__name__}: {e}"}
                yield {"event": "reply", "data": a["reply"]}
                yield {"event": "status", "data": "idle"}
                suppress_until = time.monotonic() + 2.0
                continue

            # Otherwise ask the LLM (guarded so 429s/5xx don’t kill the stream)
            try:
                result = await asyncio.to_thread(chat_and_route, text)
                reply_text = result.get("reply", "").strip()
                if not reply_text:
                    if DEBUG_EVENTS:
                        yield {"event": "debug", "data": "llm_empty_reply"}
                    yield {"event": "status", "data": "idle"}
                    continue
            except Exception as e:
                yield {"event": "error", "data": f"llm_error: {type(e).__name__}: {e}"}
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": "llm_error -> idle"}
                yield {"event": "status", "data": "idle"}
                suppress_until = time.monotonic() + 1.0
                continue

            try:
                speak(reply_text)
            except Exception as e:
                yield {"event": "error", "data": f"tts_error: {type(e).__name__}: {e}"}

            yield {"event": "reply", "data": reply_text}
            yield {"event": "status", "data": "idle"}
            suppress_until = time.monotonic() + 2.0

    return EventSourceResponse(gen())
    DEBUG_EVENTS = os.getenv("DEBUG_EVENTS", "0") == "1"

    async def gen():
        yield {"event": "status", "data": "idle"}

        suppress_until = 0.0
        last_ping = time.monotonic()

        while True:
            now = time.monotonic()

            if now - last_ping > 15:
                yield {"event": "ping", "data": f"{time.strftime('%Y-%m-%d %H:%M:%S')}"}
                last_ping = now

            if now < suppress_until:
                await asyncio.sleep(0.1)
                continue

            yield {"event": "status", "data": "listening"}
            try:
                wav = await asyncio.to_thread(detect_wake_and_record)
            except Exception as e:
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": f"detect_error: {type(e).__name__}: {e}"}
                await asyncio.sleep(0.3)
                yield {"event": "status", "data": "idle"}
                continue

            yield {"event": "status", "data": "processing"}
            try:
                text = await asyncio.to_thread(transcribe, wav)
            except Exception as e:
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": f"transcribe_error: {type(e).__name__}: {e}"}
                await asyncio.sleep(0.3)
                yield {"event": "status", "data": "idle"}
                continue

            wake = heard_wake(text)
            # send transcript as JSON with wake flag so UI can decide what to show
            payload = json.dumps({"text": text or "", "wake": wake})
            yield {"event": "transcript", "data": payload}

            if not wake:
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": "no_wake -> idle"}
                yield {"event": "status", "data": "idle"}
                continue

            # Local actions first
            try:
                a = act.maybe_handle(text)
            except Exception as e:
                a = None
                yield {"event": "error", "data": f"action_router_error: {type(e).__name__}: {e}"}

            if a and a.get("reply"):
                yield {"event": "action", "data": str(a)}
                try:
                    speak(a["reply"])
                except Exception as e:
                    yield {"event": "error", "data": f"tts_error: {type(e).__name__}: {e}"}
                yield {"event": "reply", "data": a["reply"]}
                yield {"event": "status", "data": "idle"}
                suppress_until = time.monotonic() + 2.0
                continue

            # Otherwise ask the LLM (guarded so 429s/5xx don’t kill the stream)
            try:
                result = await asyncio.to_thread(chat_and_route, text)
                reply_text = result.get("reply", "").strip()
                if not reply_text:
                    if DEBUG_EVENTS:
                        yield {"event": "debug", "data": "llm_empty_reply"}
                    yield {"event": "status", "data": "idle"}
                    continue
            except Exception as e:
                yield {"event": "error", "data": f"llm_error: {type(e).__name__}: {e}"}
                if DEBUG_EVENTS:
                    yield {"event": "debug", "data": "llm_error -> idle"}
                yield {"event": "status", "data": "idle"}
                suppress_until = time.monotonic() + 1.0
                continue

            try:
                speak(reply_text)
            except Exception as e:
                yield {"event": "error", "data": f"tts_error: {type(e).__name__}: {e}"}

            yield {"event": "reply", "data": reply_text}
            yield {"event": "status", "data": "idle"}
            suppress_until = time.monotonic() + 2.0

    return EventSourceResponse(gen())
    async def gen():
        yield {"event": "status", "data": "idle"}

        suppress_until = 0.0          # suppress mic after TTS
        llm_cooldown_until = 0.0      # --- NEW: global LLM cooldown after 429
        last_ping = time.monotonic()

        while True:
            now = time.monotonic()

            # keepalive ping every ~15s
            if now - last_ping > 15:
                yield {"event": "ping", "data": time.strftime("%Y-%m-%d %H:%M:%S")}
                last_ping = now

            # suppression window (avoid hearing our TTS)
            if now < suppress_until:
                await asyncio.sleep(IDLE_LOOP_SLEEP)
                continue

            # capture one utterance
            yield {"event": "status", "data": "listening"}
            try:
                wav = await asyncio.to_thread(detect_wake_and_record)
            except Exception as e:
                yield {"event": "error", "data": f"detect_error: {type(e).__name__}: {e}"}
                await asyncio.sleep(0.3)
                yield {"event": "status", "data": "idle"}
                continue

            yield {"event": "status", "data": "processing"}
            try:
                text = await asyncio.to_thread(transcribe, wav)
            except Exception as e:
                yield {"event": "error", "data": f"transcribe_error: {type(e).__name__}: {e}"}
                await asyncio.sleep(0.3)
                yield {"event": "status", "data": "idle"}
                continue

            if heard_wake(text):
                yield {"event": "transcript", "data": text or ""}
            else:
                # stay quiet in UI; just go idle again
                yield {"event": "status", "data": "idle"}
                continue

            # Local actions FIRST
            try:
                a = act.maybe_handle(text)
            except Exception as e:
                a = None
                yield {"event": "error", "data": f"action_router_error: {type(e).__name__}: {e}"}

            if a and a.get("reply"):
                yield {"event": "action", "data": str(a)}
                try:
                    speak(a["reply"])
                except Exception as e:
                    yield {"event": "error", "data": f"tts_error: {type(e).__name__}: {e}"}
                yield {"event": "reply", "data": a["reply"]}
                yield {"event": "status", "data": "idle"}
                suppress_until = time.monotonic() + 2.0
                continue

            # If LOCAL_ONLY or under cooldown, skip LLM
            if LOCAL_ONLY or time.monotonic() < llm_cooldown_until:
                if time.monotonic() < llm_cooldown_until:
                    remaining = max(0, int(llm_cooldown_until - time.monotonic()))
                    yield {"event": "error", "data": f"llm_cooldown_active: {remaining}s left"}
                yield {"event": "status", "data": "idle"}
                await asyncio.sleep(IDLE_LOOP_SLEEP)
                continue

            # Otherwise ask the LLM
            try:
                result = await asyncio.to_thread(chat_and_route, text)
                if result.get("error") == "rate_limited":
                    yield {"event": "error", "data": "llm_error: rate_limited"}
                    # --- NEW: set global cooldown
                    llm_cooldown_until = time.monotonic() + LLM_COOLDOWN_SEC
                    yield {"event": "status", "data": "idle"}
                    continue

                reply_text = (result.get("reply") or "").strip()
            except Exception as e:
                yield {"event": "error", "data": f"llm_error: {type(e).__name__}: {e}"}
                yield {"event": "status", "data": "idle"}
                # short suppression to avoid hot loop on generic failures
                suppress_until = time.monotonic() + 3.0
                continue

            try:
                speak(reply_text)
            except Exception as e:
                yield {"event": "error", "data": f"tts_error: {type(e).__name__}: {e}"}

            yield {"event": "reply", "data": reply_text}
            yield {"event": "status", "data": "idle"}
            suppress_until = time.monotonic() + 2.0

    return EventSourceResponse(gen())