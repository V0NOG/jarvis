# backend/app/brain.py
import os, time, random, requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("CHAT_MODEL", "gpt-4o-mini"))

API_URL = "https://api.openai.com/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
}

def _retry_delays():
    base = 1.0
    for _ in range(6):
        yield base
        base *= 2.0

def chat_and_route(text: str) -> dict:
    if not OPENAI_API_KEY:
        return {"reply": "No API key configured."}

    body = {
        "model": LLM_MODEL,
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
                retry_after = r.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after) if retry_after is not None else None
                except Exception:
                    retry_after = None
                # Distinguish quota vs. rate-limit
                try:
                    msg = r.json().get("error", {}).get("message", "")
                except Exception:
                    msg = ""
                kind = "quota_exceeded" if "quota" in msg.lower() else "rate_limited"
                return {"error": kind, "retry_after": retry_after, "message": msg}

            r.raise_for_status()
            data = r.json()
            reply = data["choices"][0]["message"]["content"].strip()
            return {"reply": reply}

        except requests.HTTPError as e:
            last_exc = e
        except Exception as e:
            last_exc = e

        time.sleep(delay + random.random())

    if last_exc:
        raise last_exc
    return {"reply": "Sorry, something went wrong."}