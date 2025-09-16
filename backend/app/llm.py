# backend/app/llm.py
import os, asyncio, time, random
from typing import List, Dict, Any

# If you use OpenAI:
from openai import AsyncOpenAI, RateLimitError, APIError, APITimeoutError
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Soft client-side throttles so you don't trip provider limits
RPM = int(os.getenv("OPENAI_RPM", "2"))          # requests per minute
TPM = int(os.getenv("OPENAI_TPM", "40000"))      # tokens per minute, rough estimate

# Model config
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "300"))
LLM_TEMP = float(os.getenv("LLM_TEMP", "0.6"))

# ---- Simple token bucket for TPM (very rough, word-based estimate) ----
class TokenBucket:
    def __init__(self, rate_per_min: int, capacity: int):
        self.rate = float(rate_per_min)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.t0 = time.monotonic()
        self.lock = asyncio.Lock()

    async def consume(self, n_tokens: int):
        if self.rate <= 0:
            return
        async with self.lock:
            now = time.monotonic()
            # refill
            self.tokens = min(self.capacity, self.tokens + (now - self.t0) * (self.rate / 60.0))
            self.t0 = now
            # wait until we have enough tokens
            while self.tokens < n_tokens:
                deficit = n_tokens - self.tokens
                sleep_s = max(0.05, min(5.0, (deficit / self.rate) * 60.0))
                await asyncio.sleep(sleep_s)
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.t0) * (self.rate / 60.0))
                self.t0 = now
            self.tokens -= n_tokens

tpm_bucket = TokenBucket(TPM, TPM) if TPM > 0 else None
rpm_sem = asyncio.Semaphore(RPM) if RPM > 0 else None

def _rough_token_estimate(messages: List[Dict[str, Any]]) -> int:
    # crude: 1.3 * words
    words = 0
    for m in messages:
        c = m.get("content") or ""
        if isinstance(c, list):
            c = " ".join(part.get("text","") for part in c if isinstance(part, dict))
        words += len(str(c).split())
    return int(words * 1.3)

async def call_chat(messages: List[Dict[str, Any]], *, model: str = None, max_tokens: int = None, temperature: float = None):
    model = model or LLM_MODEL
    max_tokens = max_tokens or LLM_MAX_TOKENS
    temperature = LLM_TEMP if temperature is None else temperature

    est_prompt_tokens = _rough_token_estimate(messages)
    if tpm_bucket:
        await tpm_bucket.consume(max(1, est_prompt_tokens))

    if rpm_sem:
        await rpm_sem.acquire()
    try:
        # Retry with jitter; respect Retry-After if present
        for attempt in range(5):
            try:
                resp = await _openai.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                )
                return resp
            except RateLimitError as e:
                retry_after = 0
                try:
                    retry_after = int(getattr(e, "response", None).headers.get("retry-after", "0"))
                except Exception:
                    pass
                backoff = retry_after or (2 ** attempt) + random.random()
                await asyncio.sleep(min(backoff, 12))
            except (APITimeoutError,) as e:
                await asyncio.sleep(1.5 * (attempt + 1))
            except APIError as e:
                # 5xx -> retry, others raise
                code = getattr(e, "status_code", None)
                if code and 500 <= code < 600:
                    await asyncio.sleep(1.5 * (attempt + 1))
                else:
                    raise
        raise RuntimeError("llm_error: retries_exhausted")
    finally:
        if rpm_sem:
            rpm_sem.release()