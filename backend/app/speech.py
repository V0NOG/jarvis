# backend/app/speech.py
import io, queue, wave, os, tempfile, re
import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel

from .config import SAMPLE_RATE, MODEL, DEVICE_INDEX, VAD_AGGRESSIVENESS

# -------------------
# Env / modes
# -------------------
USE_WAKEWORD = os.getenv("USE_WAKEWORD", "0") == "1"
WAKEWORD_ENGINE = os.getenv("WAKEWORD_ENGINE", "text").lower()  # "text" | "oww"
WAKEWORDS = [w.strip().lower() for w in os.getenv("WAKEWORDS", "jarvis").split(",") if w.strip()]
WAKE_PREVIEW_MS = int(os.getenv("WAKE_PREVIEW_MS", "3500"))
WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.7"))      # for OWW only
MIN_RMS = float(os.getenv("MIN_RMS", "0.0008"))                 # level gate for wake preview
MIN_UTTER_MS = int(os.getenv("MIN_UTTER_MS", "500"))
DEBUG_SPEECH = os.getenv("DEBUG_SPEECH", "0") == "1"
DEBUG_SAVE_PREVIEW = os.getenv("DEBUG_SAVE_PREVIEW", "0") == "1"
WAKE_STT_MODEL = os.getenv("WAKE_STT_MODEL", "base.en")
END_SILENCE_MS = int(os.getenv("END_SILENCE_MS", "800"))
MIN_UTTERANCE_MS = int(os.getenv("MIN_UTTERANCE_MS", "400"))

# -------------------
# STT models
# -------------------
# Main STT for full utterances
whisper = WhisperModel(MODEL, compute_type="int8", cpu_threads=4)

# Wake STT model (English-only is more reliable for short phrases)
whisper_wake = None
if USE_WAKEWORD and WAKEWORD_ENGINE == "text":
    whisper_wake = WhisperModel(WAKE_STT_MODEL, compute_type="int8", cpu_threads=4)

vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

# -------------------
# Optional OWW (not recommended on mac x86_64 without extra deps)
# -------------------
oww = None
if USE_WAKEWORD and WAKEWORD_ENGINE == "oww":
    try:
        from openwakeword.model import Model as OWWModel
        oww = OWWModel()
    except Exception as e:
        print("WARN: OpenWakeWord unavailable -> falling back to text wake. Reason:", e)
        WAKEWORD_ENGINE = "text"
        whisper_wake = WhisperModel("tiny", compute_type="int8", cpu_threads=4)

# ---------------
# Audio plumbing
# ---------------
audio_q = queue.Queue()
FRAME_MS = 20
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

def _log_device_once():
    try:
        dev = sd.query_devices(DEVICE_INDEX)
        print(
            "[speech] config:",
            f"USE_WAKEWORD={USE_WAKEWORD}",
            f"ENGINE={WAKEWORD_ENGINE}",
            f"MIN_RMS={MIN_RMS}",
            f"WAKE_PREVIEW_MS={WAKE_PREVIEW_MS}",
            f"VAD_AGGR={VAD_AGGRESSIVENESS}",
            f"MODEL={MODEL}",
        )
        print(f"[speech] using input device #{DEVICE_INDEX} (name={dev.get('name')}, max_in_ch={dev.get('max_input_channels')}, default_rate={dev.get('default_samplerate')})")
    except Exception as e:
        print(f"[speech] warning: could not query device #{DEVICE_INDEX}: {e}")

def _callback(indata, frames, time_info, status):
    if status and DEBUG_SPEECH:
        print(f"[speech] portaudio status: {status}")
    # ensure mono
    mono = indata.mean(axis=1)
    int16 = np.clip(mono * 32767, -32768, 32767).astype(np.int16)
    audio_q.put(int16.tobytes())

def listen_stream():
    _log_device_once()
    return sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="float32",
        callback=_callback,
        device=DEVICE_INDEX
    )

# -------------------
# Helpers
# -------------------
def pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()

def _rms_int16(pcm_bytes: bytes) -> float:
    if not pcm_bytes:
        return 0.0
    x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x)) if x.size else 0.0)

def _maybe_save_preview(preview_pcm: bytes):
    if not DEBUG_SAVE_PREVIEW:
        return
    p = os.path.join(tempfile.gettempdir(), "wake_preview.wav")
    try:
        with open(p, "wb") as f:
            f.write(pcm_to_wav(preview_pcm, SAMPLE_RATE))
        print(f"[speech] saved preview -> {p}")
    except Exception as e:
        print(f"[speech] failed to save preview: {e}")

# --- fuzzy wake matching (Jarvis variants) ---
FUZZY_ALIASES = [
    "jarvis", "hey jarvis", "jervis", "jarvus", "jarviss", "jarviz",
    "jobis", "jarbis", "travis", "chavis", "javis", "jar fish", "jar vis",
    "computer", "hey computer"
]
WAKE_PATTERNS = [
    r"\b(hey\s+)?jarvis\b",
    r"\bcomputer\b",
]

def _text_matches_wake(text: str) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    if any(re.search(pat, t) for pat in WAKE_PATTERNS):
        return True
    for alias in FUZZY_ALIASES:
        if alias in t:
            return True
    core = re.sub(r"[^a-z]", "", t)
    if core.startswith("jarvis") or "jarvis".startswith(core[:5]):
        return True
    return False

# -------------------
# Wakeword checkers (kept for debugging / future)
# -------------------
def _oww_hit(chunk_bytes: bytes) -> bool:
    if not oww:
        return False
    samples = np.frombuffer(chunk_bytes, dtype=np.int16)
    scores = oww.predict(samples, SAMPLE_RATE) or {}
    if not scores:
        return False
    if WAKEWORDS:
        for k, v in scores.items():
            if any(kw in k.lower() for kw in WAKEWORDS) and v >= WAKE_THRESHOLD:
                return True
        return False
    return max(scores.values()) >= WAKE_THRESHOLD

def _text_wake_hit(preview_pcm: bytes) -> bool:
    """Check last N ms of audio for wake using a small Whisper model."""
    if not whisper_wake:
        return False

    # 1) Level gate
    rms = _rms_int16(preview_pcm)
    if rms < MIN_RMS:
        if DEBUG_SPEECH:
            print(f"[speech] wake reject: rms {rms:.4f} < MIN_RMS {MIN_RMS}")
        return False

    # 2) VAD gate (require some voiced content)
    voiced_frames = 0
    total_frames = 0
    ptr = 0
    frame_bytes = FRAME_SAMPLES * 2
    while ptr + frame_bytes <= len(preview_pcm):
        frame = preview_pcm[ptr:ptr+frame_bytes]
        ptr += frame_bytes
        total_frames += 1
        try:
            if vad.is_speech(frame, SAMPLE_RATE):
                voiced_frames += 1
        except Exception:
            pass

    voiced_ratio = (voiced_frames / total_frames) if total_frames else 0.0
    THRESH = 0.20
    if voiced_ratio < THRESH:
        if DEBUG_SPEECH:
            print(f"[speech] wake reject: voiced_ratio {voiced_ratio:.2f} < {THRESH:.2f}")
        return False

    _maybe_save_preview(preview_pcm)

    # 3) Transcribe the preview with a tiny beam
    def run_wake_transcribe(vad_filter: bool) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            f.write(pcm_to_wav(preview_pcm, SAMPLE_RATE))
            f.flush()
            segs, _ = whisper_wake.transcribe(
                f.name,
                language="en",
                vad_filter=vad_filter,
                beam_size=3,
                temperature=0.0,
                condition_on_previous_text=False,
                initial_prompt="Wake phrase variants: hey jarvis, jarvis, computer."
            )
            return " ".join(s.text for s in segs).strip().lower()

    text = run_wake_transcribe(vad_filter=True) or run_wake_transcribe(vad_filter=False)
    hit = _text_matches_wake(text)

    if DEBUG_SPEECH:
        print(f"[speech] wake preview text='{text}' -> hit={hit} (rms={rms:.4f}, voiced={voiced_ratio:.2f})")

    # 4) Soft fallback if we clearly heard voice but text came back empty
    if not hit and voiced_ratio >= 0.25:
        if DEBUG_SPEECH:
            print("[speech] wake soft-hit by voiced_ratio fallback")
        return True

    return hit

def _wake_gate(latest_chunk: bytes, wake_ring: bytes) -> bool:
    """Return True when wakeword is detected in the preview window (unused in always-capture mode)."""
    if WAKEWORD_ENGINE == "oww":
        return _oww_hit(latest_chunk)
    max_bytes = int(SAMPLE_RATE * (WAKE_PREVIEW_MS / 1000.0)) * 2
    preview = wake_ring[-max_bytes:]
    return _text_wake_hit(preview)

# -------------------
# Main capture flow (ALWAYS capture one utterance)
# -------------------
def detect_wake_and_record(max_silence_ms=1200, pre_roll_ms=300, min_voiced_ms=None):
    """
    ALWAYS capture one utterance (no preview wake gate).
    The caller will run transcribe() and then heard_wake(text) to decide whether to act.
    """
    if max_silence_ms is None:
        max_silence_ms = END_SILENCE_MS
    if min_voiced_ms is None:
        min_voiced_ms = max(MIN_UTTER_MS, MIN_UTTERANCE_MS)  # keep your old knob but prefer env

    with listen_stream():
        ring = b""         # consumed by VAD
        wake_ring = b""    # kept for optional debugging
        in_utterance = True  # <-- always capture
        frames = []
        silence_ms = 0
        voiced_ms = 0
        pre_roll_frames = int(pre_roll_ms / FRAME_MS)
        pre_ring = []

        while True:
            chunk = audio_q.get()

            # Live heartbeat of input levels
            if DEBUG_SPEECH:
                r = _rms_int16(chunk)
                if r < 0.0008:
                    print(f"[speech] chunk rms={r:.4f} (very low)")
                else:
                    print(f"[speech] chunk rms={r:.4f}")

            # feed both buffers
            ring += chunk
            wake_ring += chunk

            # trim wake_ring to last WAKE_PREVIEW_MS (debug only)
            max_wake_bytes = int(SAMPLE_RATE * (WAKE_PREVIEW_MS / 1000.0)) * 2
            if len(wake_ring) > max_wake_bytes:
                wake_ring = wake_ring[-max_wake_bytes:]

            # Process fixed 20ms frames for VAD / capture
            while len(ring) >= FRAME_SAMPLES * 2:
                frame = ring[: FRAME_SAMPLES * 2]
                ring = ring[FRAME_SAMPLES * 2 :]

                # rolling pre-roll
                pre_ring.append(frame)
                if len(pre_ring) > pre_roll_frames:
                    pre_ring.pop(0)

                try:
                    voiced = vad.is_speech(frame, SAMPLE_RATE)
                except Exception:
                    voiced = False

                if in_utterance:
                    if voiced:
                        if voiced_ms == 0:
                            frames.extend(pre_ring)  # include pre-roll at the start
                        frames.append(frame)
                        voiced_ms += FRAME_MS
                        silence_ms = 0
                    else:
                        if voiced_ms >= min_voiced_ms:
                            silence_ms += FRAME_MS
                            frames.append(frame)
                            if silence_ms >= max_silence_ms:
                                if DEBUG_SPEECH:
                                    secs = len(b"".join(frames)) / (2 * SAMPLE_RATE)
                                    print(f"[speech] END utterance: ~{secs:.2f}s audio")
                                return pcm_to_wav(b"".join(frames), SAMPLE_RATE)
                        # else: still waiting for first voiced frame

# -------------------
# Full-utterance transcription
# -------------------
def transcribe(wav_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        f.write(wav_bytes)
        f.flush()
        segments, _ = whisper.transcribe(
            f.name,
            language="en",
            vad_filter=False,
            beam_size=5,
            condition_on_previous_text=False,
            initial_prompt="You are transcribing short voice commands like 'Jarvis' and simple questions."
        )
        return " ".join(s.text.strip() for s in segments).strip()

# -------------------
# Wake phrase check on full text (for /listen-once & routing)
# -------------------
def heard_wake(text: str) -> bool:
    return _text_matches_wake(text or "")