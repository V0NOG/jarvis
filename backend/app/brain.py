# backend/app/brain.py
import os, time, random, requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")  # lighter by default

API_URL = "https://api.openai.com/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
}

def _retry_delays():
    # longer exponential backoff + jitter
    base = 1.0
    for i in range(6):  # up to ~60s total
        yield base
        base *= 2.0

def chat_and_route(text: str) -> dict:
    """Call LLM with decent backoff. Returns {'reply': str}."""
    if not OPENAI_API_KEY:
        return {"reply": "No API key configured."}

    body = {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": "You are a concise voice assistant."},
            {"role": "user", "content": text or ""},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
    }

    last_exc = None
    for delay in _retry_delays():
        try:
            r = requests.post(API_URL, headers=HEADERS, json=body, timeout=30)
            if r.status_code == 429:
                # Return a sentinel so main can cool down.
                return {"error": "rate_limited"}
            r.raise_for_status()
            data = r.json()
            reply = data["choices"][0]["message"]["content"].strip()
            return {"reply": reply}
        except requests.HTTPError as e:
            # Non-429: retry a couple times; then bubble up
            last_exc = e
        except Exception as e:
            last_exc = e

        # jittered sleep before retry
        time.sleep(delay + random.random())

    if last_exc:
        raise last_exc
    return {"reply": "Sorry, something went wrong."}