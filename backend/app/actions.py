# backend/app/actions.py
# Intentionally a no-op so EVERYTHING goes to the LLM after the wake word.
from typing import Optional, Dict

def maybe_handle(text: str) -> Optional[Dict[str, str]]:
    return None