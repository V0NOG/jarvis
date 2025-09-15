import os, subprocess, tempfile

VOICE = os.getenv("MAC_TTS_VOICE", "Alex")


def speak(text: str):
    # macOS built-in for zero-dependency start
    subprocess.Popen(["say", "-v", VOICE, text])

    # Later (Pi 5): use Piper
    # def speak_with_piper(text: str):
    # ... generate wav -> aplay