# backend/scripts/test_wake.py

import sys
import os

# Make sure Python can see the parent folder (backend)
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.speech import detect_wake_and_record, transcribe

print("Talk now (no wake word). Pause for ~1s to end…")
wav = detect_wake_and_record()
print("Captured. Transcribing…")
print(transcribe(wav))