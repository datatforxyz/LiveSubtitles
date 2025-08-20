# pipeline.py
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from rich.console import Console

import argostranslate.package as argos_pkg
import argostranslate.translate as argos_translate

console = Console()

SAMPLE_RATE = 16000
BLOCKSIZE = 16000       # ~1s
DTYPE = "int16"
CHANNELS = 1

WINDOW_S = 5.0
OVERLAP_S = 1.0
WHISPER_MODEL = "base.en"
WHISPER_COMPUTE = "int8"
CHAR_WRAP = 120  # the overlay text layer wraps; keep lines sane

def _ensure_argos_en_es():
    langs = argos_translate.get_installed_languages()
    en = next((l for l in langs if l.code == "en"), None)
    es = next((l for l in langs if l.code == "es"), None)
    if en and es and en.get_translation(es):
        return

    console.print("[yellow]Installing Argos EN→ES translation package (one-time)...[/yellow]")
    argos_pkg.update_package_index()
    pkg = next(p for p in argos_pkg.get_available_packages()
               if p.from_code == "en" and p.to_code == "es")
    argos_pkg.install_from_path(pkg.download())
    console.print("[green]Argos EN→ES installed.[/green]")

def _translator():
    langs = argos_translate.get_installed_languages()
    en = next(l for l in langs if l.code == "en")
    es = next(l for l in langs if l.code == "es")
    return en.get_translation(es)

def wrap(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    out = []
    line = []
    count = 0
    for w in text.split():
        if count + (1 if line else 0) + len(w) <= width:
            line.append(w)
            count += (1 if line[:-1] else 0) + len(w)
        else:
            out.append(" ".join(line))
            line = [w]
            count = len(w)
    if line:
        out.append(" ".join(line))
    return "\n".join(out)

class AudioLoop:
    def __init__(self):
        self.q = queue.Queue()
        self.buf = np.zeros(0, dtype=np.float32)
        self.lock = threading.Lock()
        self.stream = None

    def start(self):
        def cb(indata, frames, time_info, status):
            if status:
                console.log(f"[yellow]Audio status: {status}[/yellow]")
            self.q.put(bytes(indata))
        self.stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCKSIZE,
            dtype=DTYPE,
            channels=CHANNELS,
            callback=cb,
        )
        self.stream.start()

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()

    def consume(self):
        try:
            data = self.q.get(timeout=0.2)
            chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            with self.lock:
                self.buf = np.concatenate([self.buf, chunk])
                cap = int(SAMPLE_RATE * (WINDOW_S + OVERLAP_S)) * 6
                if self.buf.shape[0] > cap:
                    self.buf = self.buf[-cap:]
        except queue.Empty:
            pass

    def latest_window(self):
        need = int(SAMPLE_RATE * WINDOW_S)
        with self.lock:
            if self.buf.shape[0] < need:
                return None
            return self.buf[-need:].copy()

    def trim_overlap(self):
        keep = int(SAMPLE_RATE * OVERLAP_S)
        with self.lock:
            self.buf = self.buf[-keep:]

def run_pipeline(set_overlay_text):
    console.print("[cyan]Loading Whisper model...[/cyan]")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE)

    _ensure_argos_en_es()
    translate = _translator()

    audio = AudioLoop()
    audio.start()

    last_shown = ""
    stop = False

    try:
        next_tick = 0.0
        while not stop:
            audio.consume()
            now = time.time()
            if now < next_tick:
                time.sleep(0.03)
                continue
            next_tick = now + 0.5

            segment = audio.latest_window()
            if segment is None:
                continue

            segments, _info = model.transcribe(
                segment,
                language="en",
                vad_filter=True,
                beam_size=1,
                condition_on_previous_text=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            if not text:
                continue

            if len(text) >= 8 and text != last_shown:
                es = translate.translate(text)
                set_overlay_text(wrap(es, CHAR_WRAP))
                last_shown = text
                audio.trim_overlap()
    finally:
        audio.stop()
