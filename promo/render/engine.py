"""Motore dei video: una timeline di segmenti disegnati con Pillow, codificata con ffmpeg.

Scelte che contano:

- **1080x1920, 30 fps, H.264 + AAC.** Il formato verticale di TikTok, Reels e Shorts.
- **Zona sicura.** Nessun testo negli ultimi 170 px a destra (i pulsanti di TikTok) ne' negli
  ultimi 250 px in basso (la didascalia). `Canvas` registra il riquadro di ogni testo, e
  `check_layout()` fallisce se uno esce dalla zona: e' il test che la tiene vera.
- **Deterministico.** Nessun orologio, nessun caso non seminato, e ffmpeg in modalita'
  `bitexact` senza metadati: stesso input, stesso file byte per byte.
- **Veloce.** Le parti ferme di un fotogramma si disegnano una volta sola (`Segment.key`):
  un fotogramma identico al precedente non si ridisegna, si riscrive.
"""
import hashlib
import math
import os
import struct
import subprocess
from array import array
from dataclasses import dataclass, field
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
SAMPLE_RATE = 44100

SAFE_RIGHT = W - 170   # oltre: pulsanti di TikTok
SAFE_BOTTOM = H - 250  # oltre: didascalia
SAFE_LEFT = 60
SAFE_TOP = 100
SAFE_CENTER_X = (SAFE_LEFT + SAFE_RIGHT) // 2

FALLBACK_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


class LayoutError(AssertionError):
    """Un testo e' finito fuori dalla zona sicura."""


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def mix(a: tuple, b: tuple, t: float) -> tuple:
    """Colore fra `a` (t=0) e `b` (t=1)."""
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


class Theme:
    """Palette, colori dei club e font: tutto quello che viene dal gioco."""

    def __init__(self, palette: dict, team_color: Callable[[str], tuple], title_font: Optional[str],
                 text_font: Optional[str] = None):
        self.palette = palette
        self.team_color = team_color
        self.title_font_path = title_font or self._fallback()
        self.text_font_path = text_font or self.title_font_path
        self._fonts: dict = {}
        self._bg: Optional[Image.Image] = None

    @staticmethod
    def _fallback() -> Optional[str]:
        return next((path for path in FALLBACK_FONTS if os.path.exists(path)), None)

    @classmethod
    def from_game(cls, game) -> "Theme":
        return cls(game.palette(), game.team_color, game.title_font_path(), game.text_font_path())

    def font(self, size: int, title: bool = True):
        size = max(8, int(size))
        key = (size, title)
        if key not in self._fonts:
            path = self.title_font_path if title else self.text_font_path
            self._fonts[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)
        return self._fonts[key]

    def background(self) -> Image.Image:
        if self._bg is None:
            top, bottom = self.palette["bg_top"], self.palette["bg_bottom"]
            column = Image.new("RGB", (1, H))
            column.putdata([mix(top, bottom, y / H) for y in range(H)])
            self._bg = column.resize((W, H))
        return self._bg

    def bg_at(self, y: float) -> tuple:
        return mix(self.palette["bg_top"], self.palette["bg_bottom"], max(0.0, min(1.0, y / H)))


class Canvas:
    """Un fotogramma RGB con i metodi di disegno che registrano i riquadri dei testi."""

    def __init__(self, image: Image.Image, theme: Theme, boxes: Optional[list] = None):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.theme = theme
        self.boxes = boxes

    def fit(self, text: str, size: int, max_width: int, title: bool = True):
        font = self.theme.font(size, title)
        while size > 12 and self.draw.textlength(text, font=font) > max_width:
            size = int(size * 0.94)
            font = self.theme.font(size, title)
        return font

    def _record(self, box):
        if self.boxes is not None:
            self.boxes.append(tuple(int(v) for v in box))

    def text(self, xy, text: str, font, fill, alpha: float = 1.0):
        x, y = xy
        color = fill if alpha >= 1 else mix(self.theme.bg_at(y), fill, alpha)
        self.draw.text((x, y), text, font=font, fill=color)
        self._record(self.draw.textbbox((x, y), text, font=font))

    def centered(self, y: float, text: str, size: int, fill=None, *, alpha: float = 1.0,
                 title: bool = True, max_width: Optional[int] = None) -> None:
        fill = fill or self.theme.palette["text"]
        max_width = max_width or (SAFE_RIGHT - SAFE_LEFT)
        font = self.fit(text, size, max_width, title)
        width = self.draw.textlength(text, font=font)
        self.text((SAFE_CENTER_X - width / 2, y), text, font, fill, alpha)


@dataclass
class Segment:
    duration: float
    draw: Callable  # (canvas, t_0_1, frame_index) -> None
    key: Optional[Callable] = None  # (t_0_1, frame_index) -> chiave hashable per riusare il fotogramma


@dataclass
class Tone:
    at: float
    freq: float
    duration: float
    volume: float


@dataclass
class Timeline:
    segments: list = field(default_factory=list)
    tones: list = field(default_factory=list)
    cover_at: float = 1.0

    def add(self, duration: float, draw: Callable, key: Optional[Callable] = None) -> float:
        start = self.total
        self.segments.append(Segment(duration, draw, key))
        return start

    def tone(self, at: float, freq: float, duration: float = 0.12, volume: float = 0.35) -> None:
        self.tones.append(Tone(at, freq, duration, volume))

    @property
    def total(self) -> float:
        return sum(segment.duration for segment in self.segments)

    def frame_ranges(self):
        """(segmento, primo fotogramma, numero di fotogrammi), senza deriva di arrotondamento."""
        start = 0.0
        for segment in self.segments:
            first = round(start * FPS)
            last = round((start + segment.duration) * FPS)
            yield segment, first, last - first
            start += segment.duration

    @property
    def frame_count(self) -> int:
        return round(self.total * FPS)


def synth_audio(timeline: Timeline, frames: int) -> bytes:
    """Effetti sintetizzati (niente musica protetta): WAV mono 16 bit."""
    samples = int(frames / FPS * SAMPLE_RATE)
    buffer = [0.0] * samples
    for tone in timeline.tones:
        start = int(tone.at * SAMPLE_RATE)
        length = int(tone.duration * SAMPLE_RATE)
        decay = SAMPLE_RATE * tone.duration / 5
        step = 2 * math.pi * tone.freq / SAMPLE_RATE
        for j in range(min(length, max(0, samples - start))):
            buffer[start + j] += tone.volume * math.exp(-j / decay) * math.sin(step * j)
    pcm = array("h", (int(max(-1.0, min(1.0, v)) * 32000) for v in buffer))
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm) * 2) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(pcm) * 2)
    return header + pcm.tobytes()


def ffmpeg_exe() -> str:
    override = os.environ.get("PROMO_FFMPEG")
    if override:
        return override
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def render_frame(timeline: Timeline, theme: Theme, seconds: float, boxes: Optional[list] = None) -> Image.Image:
    """Un solo fotogramma (per la copertina e per i controlli di impaginazione)."""
    target = min(round(seconds * FPS), max(timeline.frame_count - 1, 0))
    for segment, first, count in timeline.frame_ranges():
        if first <= target < first + count:
            j = target - first
            canvas = Canvas(theme.background().copy(), theme, boxes)
            segment.draw(canvas, j / max(count - 1, 1), target)
            return canvas.image
    raise ValueError(f"nessun fotogramma a {seconds}s")


def check_layout(timeline: Timeline, theme: Theme, step: float = 0.25) -> None:
    """Controlla la zona sicura su un fotogramma ogni `step` secondi."""
    t = 0.0
    while t < timeline.total:
        boxes: list = []
        render_frame(timeline, theme, t, boxes)
        for box in boxes:
            left, top, right, bottom = box
            if right > SAFE_RIGHT or bottom > SAFE_BOTTOM or left < 0 or top < 0:
                raise LayoutError(f"testo fuori dalla zona sicura a {t:.2f}s: {box}")
        t += step


def encode(timeline: Timeline, theme: Theme, video_path: str, cover_path: str, *, preset: str = "medium",
           crf: int = 21) -> str:
    """Scrive MP4 e copertina PNG. Ritorna lo sha256 del video."""
    os.makedirs(os.path.dirname(os.path.abspath(video_path)), exist_ok=True)
    frames = timeline.frame_count
    wav_path = video_path + ".wav"
    with open(wav_path, "wb") as fh:
        fh.write(synth_audio(timeline, frames))

    command = [
        ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", wav_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", preset, "-crf", str(crf),
        "-profile:v", "high", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE),
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        "-map_metadata", "-1", "-movflags", "+faststart", "-shortest",
        video_path,
    ]
    cover_frame = min(round(timeline.cover_at * FPS), frames - 1)
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        background = theme.background()
        last_key, last_bytes = object(), b""
        for segment, first, count in timeline.frame_ranges():
            for j in range(count):
                index = first + j
                t = j / max(count - 1, 1)
                key = segment.key(t, index) if segment.key else None
                if key is not None and key == last_key and index != cover_frame:
                    process.stdin.write(last_bytes)
                    continue
                canvas = Canvas(background.copy(), theme)
                segment.draw(canvas, t, index)
                data = canvas.image.tobytes()
                process.stdin.write(data)
                last_key, last_bytes = (key if key is not None else object()), data
                if index == cover_frame:
                    canvas.image.save(cover_path, optimize=False)
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError(f"ffmpeg e' terminato con codice {process.returncode}")
    finally:
        if process.poll() is None:
            process.kill()
        if os.path.exists(wav_path):
            os.remove(wav_path)

    digest = hashlib.sha256()
    with open(video_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
