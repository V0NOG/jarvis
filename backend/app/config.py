# backend/app/config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# Load both: project root .env (when running from repo root) and app-local .env (optional)
load_dotenv()  # loads ./.env
load_dotenv(Path(__file__).parent / ".env")  # loads backend/app/.env if present

# Exported settings used across the app
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("WHISPER_MODEL", "small")  # tiny|base|small|medium|large-v3
WAKEWORDS = os.getenv("WAKEWORDS", "jarvis,computer").split(",")
DEVICE_INDEX = int(os.getenv("DEVICE_INDEX", "-1"))  # -1 = default mic
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
VAD_AGGRESSIVENESS = int(os.getenv("VAD_AGGRESSIVENESS", "2"))

# Optional extras some files read
VOICE = os.getenv("MAC_TTS_VOICE", "Alex")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1")