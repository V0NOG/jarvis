# backend/app/main.py
import os, json, asyncio, time
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
from fastapi.middleware.cors import CORSMiddleware

from .speech import detect_wake_and_record, transcribe, heard_wake
from .brain import chat_and_route
from .tts import speak

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- env knobs ---
LOCAL_ONLY = os.getenv("LOCAL_ONLY", "0") == "1"                 # if 1, never call LLM
LLM_COOLDOWN_SEC = float(os.getenv("LLM_COOLDOWN_SEC", "45"))    # cooldown after 429
IDLE_LOOP_SLEEP = float(os.getenv("IDLE_LOOP_SLEEP", "0.15"))    # idle loop sleep
PING_INTERVAL_SEC = float(os.getenv("PING_INTERVAL_SEC", "15"))  # SSE keep-alive
DEBUG_EVENTS = os.getenv("DEBUG_EVENTS", "0") == "1"

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/listen-once")
async def listen_once():
    wav = await asyncio.to_thread(detect_wake_and_record, max_silence_ms=800)
    text = await asyncio.to_thread(transcribe, wav)
    return {"transcript": text, "wake": heard_wake(text)}

@app.get("/events")
async def events(request: Request):
    """
    Server-Sent Events stream.
    - Never drop the SSE connection on 429 (rate limit). Stay connected and emit 'app_error' + 'status: idle'.
    - Send keep-alive pings to prevent proxies from idling out the stream.
    """
    async def gen():
        # helper: safe yield that exits cleanly if client is gone
        async def emit(event: str, data):
            if await request.is_disconnected():
                raise asyncio.CancelledError()
            text = data if isinstance(data, str) else json.dumps(data)
            yield {"event": event, "data": text}

        # first status
        async for chunk in emit("status", "idle"):
            yield chunk

        suppress_until = 0.0           # suppress mic while TTS speaks
        llm_cooldown_until = 0.0       # cooldown if LLM rate-limits
        last_ping = time.monotonic()

        try:
            while True:
                now = time.monotonic()

                # keep-alive ping every ~PING_INTERVAL_SEC
                if now - last_ping > PING_INTERVAL_SEC:
                    human = time.strftime("%Y-%m-%d %H:%M:%S")
                    async for chunk in emit("ping", human):
                        yield chunk
                    last_ping = now

                # suppression window (avoid hearing our own TTS)
                if now < suppress_until:
                    await asyncio.sleep(IDLE_LOOP_SLEEP)
                    continue

                # capture one utterance
                async for chunk in emit("status", "listening"):
                    yield chunk

                try:
                    wav = await asyncio.to_thread(detect_wake_and_record)
                except Exception as e:
                    if DEBUG_EVENTS:
                        async for chunk in emit("debug", f"detect_error: {type(e).__name__}: {e}"):
                            yield chunk
                    await asyncio.sleep(0.3)
                    async for chunk in emit("status", "idle"):
                        yield chunk
                    continue

                # transcribe it
                async for chunk in emit("status", "processing"):
                    yield chunk
                try:
                    text = await asyncio.to_thread(transcribe, wav)
                except Exception as e:
                    if DEBUG_EVENTS:
                        async for chunk in emit("debug", f"transcribe_error: {type(e).__name__}: {e}"):
                            yield chunk
                    await asyncio.sleep(0.3)
                    async for chunk in emit("status", "idle"):
                        yield chunk
                    continue

                # send transcript (with wake flag)
                wake = heard_wake(text)
                async for chunk in emit("transcript", {"text": text or "", "wake": wake}):
                    yield chunk

                # require wakeword before doing anything else
                if not wake:
                    if DEBUG_EVENTS:
                        async for chunk in emit("debug", "no_wake -> idle"):
                            yield chunk
                    async for chunk in emit("status", "idle"):
                        yield chunk
                    continue

                # optionally skip LLM entirely
                if LOCAL_ONLY:
                    if DEBUG_EVENTS:
                        async for chunk in emit("debug", "LOCAL_ONLY=1 -> skipping LLM"):
                            yield chunk
                    async for chunk in emit("status", "idle"):
                        yield chunk
                    await asyncio.sleep(IDLE_LOOP_SLEEP)
                    continue

                # honor LLM cooldown (after a 429)
                if time.monotonic() < llm_cooldown_until:
                    remaining = max(0, int(llm_cooldown_until - time.monotonic()))
                    # stay connected; just inform the UI
                    async for chunk in emit("app_error", f"llm_cooldown_active: {remaining}s left"):
                        yield chunk
                    async for chunk in emit("status", "idle"):
                        yield chunk
                    await asyncio.sleep(IDLE_LOOP_SLEEP)
                    continue

                # route to LLM
                try:
                    # chat_and_route should return {"error":"rate_limited","retry_after": seconds?} on 429
                    result = await asyncio.to_thread(chat_and_route, text)

                    # 429 path — DO NOT DROP SSE
                    if result and result.get("error") == "rate_limited":
                        retry_after = float(result.get("retry_after") or 0.0)
                        cooldown = max(LLM_COOLDOWN_SEC, retry_after)
                        llm_cooldown_until = time.monotonic() + cooldown
                        async for chunk in emit("app_error", f"llm_error: rate_limited (cooldown {int(cooldown)}s)"):
                            yield chunk
                        async for chunk in emit("status", "idle"):
                            yield chunk
                        continue

                    if result and result.get("error"):
                        msg = result.get("message") or result.get("error")
                        async for chunk in emit("app_error", f"llm_error: {msg}"):
                            yield chunk
                        async for chunk in emit("status", "idle"):
                            yield chunk
                        continue

                    reply_text = (result.get("reply") or "").strip() if result else ""
                    if not reply_text and DEBUG_EVENTS:
                        async for chunk in emit("debug", "llm_empty_reply"):
                            yield chunk

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # keep SSE alive; just log error and continue
                    async for chunk in emit("app_error", f"llm_error: {type(e).__name__}: {e}"):
                        yield chunk
                    async for chunk in emit("status", "idle"):
                        yield chunk
                    suppress_until = time.monotonic() + 2.0
                    continue

                # speak & send reply
                if reply_text:
                    try:
                        speak(reply_text)
                    except Exception as e:
                        async for chunk in emit("app_error", f"tts_error: {type(e).__name__}: {e}"):
                            yield chunk
                    async for chunk in emit("reply", reply_text):
                        yield chunk

                async for chunk in emit("status", "idle"):
                    yield chunk
                suppress_until = time.monotonic() + 2.0

        except asyncio.CancelledError:
            if DEBUG_EVENTS:
                print("[events] client disconnected")
            return
        except GeneratorExit:
            return

    return EventSourceResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )