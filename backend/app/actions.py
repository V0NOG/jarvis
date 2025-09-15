# backend/app/actions.py
from __future__ import annotations
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict

# ----------------------------------------
# City -> timezone mapping (extend as you like)
# ----------------------------------------
CITY_TZ = {
    # UK / Europe
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "amsterdam": "Europe/Amsterdam",
    "madrid": "Europe/Madrid",
    "rome": "Europe/Rome",
    "lisbon": "Europe/Lisbon",

    # US
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "denver": "America/Denver",
    "seattle": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "austin": "America/Chicago",
    "boston": "America/New_York",
    "miami": "America/New_York",

    # APAC
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane",
    "perth": "Australia/Perth",
    "auckland": "Pacific/Auckland",
    "tokyo": "Asia/Tokyo",
    "singapore": "Asia/Singapore",
    "hong kong": "Asia/Hong_Kong",

    # Other
    "dubai": "Asia/Dubai",
    "delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "toronto": "America/Toronto",
    "vancouver": "America/Vancouver",
    "mexico city": "America/Mexico_City",
}

# “in <city>” near the end of the utterance
CITY_RE = re.compile(r"\bin\s+(?:the\s+)?([a-zA-Z][a-zA-Z\s\.]+?)\b\??$", re.IGNORECASE)

def _clean(s: str) -> str:
    return (s or "").strip()

def _match_city(text: str) -> Optional[str]:
    """
    Try to find a known city name. First look for a trailing 'in <city>',
    then scan for any city token in the text.
    """
    if not text:
        return None
    t = text.lower().strip()

    m = CITY_RE.search(t)
    if m:
        candidate = re.sub(r"\s+", " ", m.group(1).strip(" .").lower())
        if candidate in CITY_TZ:
            return candidate

    for city in CITY_TZ.keys():
        if city in t:
            return city

    return None

def _fmt_time_for_tz(tz_name: str) -> str:
    now = datetime.now(ZoneInfo(tz_name))
    return now.strftime("%H:%M")

# ---------------------------
# PUBLIC ENTRY POINT
# ---------------------------
def maybe_handle(text: str) -> Optional[Dict[str, str]]:
    """
    Return an action dict when we can answer locally; otherwise return None
    so the router falls back to the LLM.
      -> {"intent": "<name>", "reply": "<text>"}
    """
    t = _clean(text).lower()
    if not t:
        return None

    # --- DATE ---
    if any(p in t for p in [
        "what's the date", "what is the date", "date today",
        "today's date", "what date is it"
    ]):
        # %-d on mac/linux, fallback-safe: strip leading zero afterward
        today = datetime.now().strftime("%A, %B %d").replace(", 0", ", ")
        return {"intent": "date", "reply": f"Today is {today}."}

    # --- TIME (local or 'time in <city>') ---
    if any(p in t for p in [
        "what's the time", "what is the time", "time is it",
        "current time", "tell me the time", "time now"
    ]):
        city = _match_city(t)
        if city:
            tz = CITY_TZ[city]
            return {"intent": "time", "reply": f"In {city.title()}, it's { _fmt_time_for_tz(tz) }."}
        else:
            here = datetime.now().strftime("%H:%M")
            return {"intent": "time", "reply": f"It's {here}."}

    # --- WEATHER (placeholder) ---
    if "weather" in t:
        city = _match_city(t)
        if city:
            return {"intent": "weather",
                    "reply": f"I don’t have a weather API configured yet, but I heard your request for {city.title()}."}
        return {"intent": "weather",
                "reply": "I don’t have a weather API configured yet, but I heard your request."}

    # Anything else -> let the LLM handle (definitions, general Q&A, etc.)
    return None