#!/usr/bin/env bash
set -euo pipefail
sudo apt update && sudo apt install -y ffmpeg python3-venv portaudio19-dev libatlas-base-dev espeak-ng aplay
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r backend/app/requirements.txt
# swap TTS to Piper + ALSA before running
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000