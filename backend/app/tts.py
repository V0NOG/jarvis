# backend/app/tts.py
import os, subprocess, tempfile, shlex

VOICE = os.getenv("MAC_TTS_VOICE", "Alex")
TTS_BACKEND = os.getenv("TTS_BACKEND", "say").lower()
PIPER_BIN = os.getenv("PIPER_BIN", "piper")  # optional if piper is on PATH
PIPER_MODEL = os.getenv("PIPER_MODEL", "")
PIPER_SPEAK_ARGS = os.getenv("PIPER_SPEAK_ARGS", "")

def speak(text: str):
    if TTS_BACKEND == "piper" and PIPER_MODEL:
        # Piper: synth to wav then play (afplay on macOS, aplay on Linux)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        cmd = f'{shlex.quote(PIPER_BIN)} -m {shlex.quote(PIPER_MODEL)} {PIPER_SPEAK_ARGS} -t {shlex.quote(text)} -o {shlex.quote(wav_path)}'
        subprocess.run(cmd, shell=True, check=False)
        player = "afplay" if os.uname().sysname == "Darwin" else "aplay"
        subprocess.Popen([player, wav_path])
        return

    # macOS built-in (default)
    subprocess.Popen(["say", "-v", VOICE, text])
