import os
import threading
import queue
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from rich.console import Console

# --- Translation (Argos) ---
import argostranslate.package as argos_pkg
import argostranslate.translate as argos_translate

# --- Simple overlay via Tkinter ---
import tkinter as tk

console = Console()

# -------------------------
# Config
# -------------------------
SAMPLE_RATE = 16000
BLOCKSIZE   = 16000            # ~1.0s blocks
DTYPE       = "int16"
CHANNELS    = 1

TRANSCRIBE_WINDOW_S = 5.0      # analyze last 5s of audio
TRANSCRIBE_OVERLAP_S = 1.0     # keep 1s overlap to avoid cutting words
WHISPER_MODEL = "base.en"      # good balance for CPU; try "small.en" for higher quality
WHISPER_COMPUTE_TYPE = "int8"  # "int8" or "float32" on CPU
SUBTITLE_MAX_CHARS = 120       # wrap the overlay a bit

# -------------------------
# Utilities
# -------------------------
def ensure_argos_en_es():
    """Ensure Argos EN->ES translation package is installed."""
    try:
        # If already present, this will succeed by creating a translator
        _ = argos_translate.get_installed_languages()
        en = next((l for l in _ if l.code == "en"), None)
        es = next((l for l in _ if l.code == "es"), None)
        if en and es and en.get_translation(es):
            return
    except Exception:
        pass

    console.print("[yellow]Installing Argos EN→ES translation package (one-time)...[/yellow]")
    argos_pkg.update_package_index()
    pkg = next(p for p in argos_pkg.get_available_packages()
               if p.from_code == "en" and p.to_code == "es")
    argos_pkg.install_from_path(pkg.download())
    console.print("[green]Argos EN→ES installed.[/green]")

def get_en_es_translator():
    langs = argos_translate.get_installed_languages()
    en = next(l for l in langs if l.code == "en")
    es = next(l for l in langs if l.code == "es")
    return en.get_translation(es)

def wrap_text(text, width):
    if len(text) <= width:
        return text
    words, lines, line = text.split(), [], []
    for w in words:
        if sum(len(x) for x in line) + len(line) - 1 + len(w) <= width:
            line.append(w)
        else:
            lines.append(" ".join(line))
            line = [w]
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines)

# -------------------------
# Subtitle Overlay (Tk)
# -------------------------
class SubtitleOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("EN→ES Subtitles")
        self.root.configure(bg="#111111")

        # Always on top, slightly transparent
        self.root.wm_attributes("-topmost", 1)
        self.root.wm_attributes("-alpha", 0.9)

        # Sizing
        self.root.geometry("900x200+100+100")
        self.label = tk.Label(
            self.root,
            text="",
            font=("Helvetica Neue", 36),
            fg="#FFFFFF",
            bg="#111111",
            justify="center",
            wraplength=860,
        )
        self.label.pack(expand=True, fill="both", padx=16, pady=16)

        # Non-blocking text updates
        self._text_queue = queue.Queue()
        self._updater()

    def _updater(self):
        try:
            while True:
                text = self._text_queue.get_nowait()
                self.label.config(text=text)
        except queue.Empty:
            pass
        self.root.after(100, self._updater)

    def set_text(self, text: str):
        self._text_queue.put(text)

    def run(self):
        self.root.mainloop()

# -------------------------
# Audio Capture + ASR Loop
# -------------------------
@dataclass
class AudioBuffer:
    buf: np.ndarray
    lock: threading.Lock

def start_audio_stream(raw_q: queue.Queue):
    def callback(indata, frames, time_info, status):
        if status:
            console.log(f"[yellow]Audio status: {status}[/yellow]")
        raw_q.put(bytes(indata))  # raw bytes, dtype int16
    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCKSIZE,
        dtype=DTYPE,
        channels=CHANNELS,
        callback=callback,
    )
    stream.start()
    return stream

def transcribe_worker(raw_q: queue.Queue, abuf: AudioBuffer, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            data = raw_q.get(timeout=0.2)
            chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            with abuf.lock:
                abuf.buf = np.concatenate([abuf.buf, chunk])
                # Keep last N seconds + overlap so buffer doesn't grow unbounded
                max_len = int(SAMPLE_RATE * (TRANSCRIBE_WINDOW_S + TRANSCRIBE_OVERLAP_S))
                if abuf.buf.shape[0] > max_len * 6:  # safety cap
                    abuf.buf = abuf.buf[-max_len:]
        except queue.Empty:
            pass

def asr_translate_worker(abuf: AudioBuffer, overlay: SubtitleOverlay, stop_event: threading.Event):
    console.print("[cyan]Loading Whisper model...[/cyan]")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE_TYPE)

    ensure_argos_en_es()
    translator = get_en_es_translator()

    last_shown = ""
    next_time = 0.0

    while not stop_event.is_set():
        now = time.time()
        if now < next_time:
            time.sleep(0.05)
            continue
        next_time = now + 0.5  # run every 0.5s

        with abuf.lock:
            audio = abuf.buf.copy()

        need = int(SAMPLE_RATE * TRANSCRIBE_WINDOW_S)
        if audio.shape[0] < need:
            continue

        # Take the last window seconds (with a little lookback to avoid chopping)
        segment = audio[-need:]
        try:
            segments, _info = model.transcribe(
                segment,
                language="en",   # input language
                vad_filter=True,
                beam_size=1,
                condition_on_previous_text=False,
            )

            # Concatenate text for this window
            text = " ".join(s.text.strip() for s in segments).strip()
            if not text:
                continue

            # Basic de-dup: only update if meaningfully changed
            if len(text) >= 8 and text != last_shown:
                es = translator.translate(text)
                display = wrap_text(es, SUBTITLE_MAX_CHARS)
                overlay.set_text(display)
                last_shown = text

                # Keep a small overlap in the rolling buffer to catch word tails
                with abuf.lock:
                    keep = int(SAMPLE_RATE * TRANSCRIBE_OVERLAP_S)
                    abuf.buf = abuf.buf[-keep:]

        except Exception as e:
            console.print(f"[red]ASR error:[/red] {e}")
            time.sleep(0.2)

# -------------------------
# Main
# -------------------------
def main():
    console.print("[green]Starting live EN→ES subtitles...[/green]")
    raw_q = queue.Queue()
    abuf = AudioBuffer(buf=np.zeros(0, dtype=np.float32), lock=threading.Lock())
    stop_event = threading.Event()

    overlay = SubtitleOverlay()

    # Threads
    t_capture = threading.Thread(target=transcribe_worker, args=(raw_q, abuf, stop_event), daemon=True)
    t_asr     = threading.Thread(target=asr_translate_worker, args=(abuf, overlay, stop_event), daemon=True)

    # Audio stream
    stream = start_audio_stream(raw_q)

    try:
        t_capture.start()
        t_asr.start()
        overlay.run()  # blocks until window is closed
    finally:
        stop_event.set()
        stream.stop()
        stream.close()

if __name__ == "__main__":
    main()
