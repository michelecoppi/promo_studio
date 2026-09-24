"""I formati video (§5.2): stessa grafica del prototipo approvato, tempi parametrizzati.

| formato      | struttura                                                             | durata |
| ------------ | --------------------------------------------------------------------- | ------ |
| `who_is`     | aggancio 2 s, tappe (~1,1 s), pausa, "3 tentativi" 3-2-1, chiusura    | 18-25  |
| `percent`    | come `who_is`, aggancio con la percentuale vera                       | 18-25  |
| `journeyman` | come `who_is` con tappe piu' rapide (10+ club)                        | 20-30  |
| `ladder`     | quattro percorsi da facile a impossibile, ~5,5 s ciascuno             | 25-30  |
| `solution`   | stesso percorso + nome rivelato                                       | 8-12   |

Rispetto al prototipo: pausa piu' lunga sull'ultima tappa, percentuale reale, tappe che
entrano dal basso invece che da destra (da destra passavano sotto i pulsanti di TikTok).
"""
import math
from typing import Optional

from PIL import Image, ImageDraw

from promo import catalog
from promo.models import Card
from promo.render.engine import (
    SAFE_BOTTOM,
    SAFE_CENTER_X,
    SAFE_RIGHT,
    Canvas,
    Theme,
    Timeline,
    ease,
    mix,
)

DURATION_LIMITS = {
    "who_is": (18.0, 25.0),
    "percent": (18.0, 25.0),
    "journeyman": (20.0, 30.0),
    "ladder": (25.0, 30.0),
    "solution": (8.0, 12.0),
}

ROWS_TOP = 330
ROWS_BOTTOM = SAFE_BOTTOM - 30
TRACK_X = 110
CARD_X = 150
TILE_X = TRACK_X - 24

TICK, BEEP, FINAL, LEVEL, CHIME = 880.0, 660.0, 1320.0, 440.0, 1046.5
LOAN_ARROW = "→"


class Rows:
    """Le tappe come righe: ogni riga e' disegnata una volta sola in un riquadro RGBA."""

    def __init__(self, theme: Theme, card: Card, top: int = ROWS_TOP, bottom: int = ROWS_BOTTOM):
        self.theme = theme
        self.stops = card.stops
        self.top = top
        n = max(len(self.stops), 1)
        self.row_h = int(min(104, (bottom - top) / n))
        self.tiles = []
        self.boxes = []
        for stop in self.stops:
            tile, boxes = self._tile(stop)
            self.tiles.append(tile)
            self.boxes.append(boxes)

    def _tile(self, stop):
        p = self.theme.palette
        h = self.row_h
        width = SAFE_RIGHT - TILE_X
        tile = Image.new("RGBA", (width, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        pad = max(4, h // 13)
        color = self.theme.team_color(stop.team)
        x0 = CARD_X - TILE_X
        d.rounded_rectangle([x0, pad, width - 1, h - pad], radius=min(22, h // 4), fill=p["card"] + (255,))
        d.rounded_rectangle([x0, pad, x0 + 14, h - pad], radius=7, fill=color + (255,))
        r = max(8, min(16, h // 6))
        cx, cy = TRACK_X - TILE_X, h / 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (255,), outline=p["text"] + (255,), width=4)

        years_font = self.theme.font(int(h * 0.38))
        # La freccia del prestito (convenzione di services/path_image.py) non esiste in
        # Barlow Condensed: la si disegna con il font di testo, il resto con quello dei titoli.
        arrow, years = "", stop.years
        if years.startswith(LOAN_ARROW):
            arrow, years = LOAN_ARROW + " ", years[len(LOAN_ARROW):].lstrip()
        arrow_font = self.theme.font(int(h * 0.34), title=False)
        arrow_w = d.textlength(arrow, font=arrow_font) if arrow else 0
        years_w = d.textlength(years, font=years_font) + arrow_w
        years_x = width - 30 - years_w
        name_max = years_x - (x0 + 40) - 20
        canvas_font = self._fit(d, stop.team, int(h * 0.5), name_max)
        name_y = (h - canvas_font.size * 1.15) / 2
        d.text((x0 + 40, name_y), stop.team, font=canvas_font, fill=p["text"] + (255,))
        years_y = (h - years_font.size * 1.15) / 2
        if arrow:
            d.text((years_x, years_y + years_font.size * 0.08), arrow, font=arrow_font, fill=p["muted"] + (255,))
        d.text((years_x + arrow_w, years_y), years, font=years_font, fill=p["muted"] + (255,))
        boxes = [
            d.textbbox((x0 + 40, name_y), stop.team, font=canvas_font),
            d.textbbox((years_x + arrow_w, years_y), years, font=years_font),
        ]
        return tile, boxes

    def _fit(self, draw, text, size, max_width):
        font = self.theme.font(size)
        while size > 14 and draw.textlength(text, font=font) > max_width:
            size = int(size * 0.94)
            font = self.theme.font(size)
        return font

    def y(self, i: int) -> int:
        return self.top + i * self.row_h

    def paint(self, canvas: Canvas, shown: int, progress: float = 1.0) -> None:
        shown = min(shown, len(self.tiles))
        if shown <= 0:
            return
        track = self.theme.palette["track"]
        canvas.draw.line(
            [(TRACK_X, self.y(0) + self.row_h / 2), (TRACK_X, self.y(shown - 1) + self.row_h / 2)],
            fill=track, width=6,
        )
        for i in range(shown):
            p = ease(progress) if i == shown - 1 else 1.0
            if p <= 0:
                continue
            tile = self.tiles[i]
            dy = int((1 - p) * 40)
            x, y = TILE_X, self.y(i) + dy
            if p >= 1:
                canvas.image.paste(tile, (x, y), tile)
            else:
                mask = tile.getchannel("A").point(lambda v, p=p: int(v * p))
                canvas.image.paste(tile, (x, y), mask)
            if p >= 0.5:
                for box in self.boxes[i]:
                    canvas._record((box[0] + x, box[1] + y, box[2] + x, box[3] + y))


# ---------------------------------------------------------------------------------------
# Pezzi comuni
# ---------------------------------------------------------------------------------------

def _hook(timeline: Timeline, theme: Theme, lines, sub: str, duration: float = 2.0) -> None:
    p = theme.palette

    def draw(canvas: Canvas, t: float, k: int) -> None:
        s = 1 + 0.04 * math.sin(k / 6)
        appear = ease(t * 4)
        sizes = (150, 110, 150)
        ys = (700, 870, 1000)
        for i, line in enumerate(lines[:3]):
            color = p["accent"] if i == 1 else p["text"]
            canvas.centered(ys[i], line, int(sizes[i] * s), color, alpha=appear)
        if sub:
            canvas.centered(1250, sub, 56, p["muted"], alpha=appear)

    timeline.add(duration, draw)


def _header(canvas: Canvas, title: str, sub: Optional[str] = None) -> None:
    canvas.centered(150, title, 84)
    if sub:
        canvas.centered(250, sub, 46, canvas.theme.palette["muted"])


def _reveal(timeline: Timeline, rows: Rows, title: str, counter: str, per_stop: float) -> None:
    n = len(rows.tiles)
    for i in range(1, n + 1):
        sub = counter.format(i=i, n=n)

        def draw(canvas, t, k, i=i, sub=sub):
            _header(canvas, title, sub)
            rows.paint(canvas, i, min(1.0, t * 3))

        def key(t, k, i=i):
            return ("reveal", id(rows), i) if t * 3 >= 1 else None

        start = timeline.add(per_stop, draw, key)
        timeline.tone(start, TICK, 0.12, 0.35)


def _hold(timeline: Timeline, rows: Rows, title: str, sub: str, duration: float) -> None:
    def draw(canvas, t, k):
        _header(canvas, title, sub)
        rows.paint(canvas, len(rows.tiles))

    timeline.add(duration, draw, lambda t, k: ("hold", id(rows), sub))


def _dimmed(theme: Theme, rows: Rows, amount: float = 0.78) -> Image.Image:
    base = Canvas(theme.background().copy(), theme)
    rows.paint(base, len(rows.tiles))
    return Image.blend(base.image, Image.new("RGB", base.image.size, (5, 10, 18)), amount)


def _countdown(timeline: Timeline, theme: Theme, rows: Rows, v: dict) -> None:
    p = theme.palette
    dimmed = _dimmed(theme, rows)

    def draw(canvas, t, k):
        canvas.image.paste(dimmed)
        n = max(3 - int(t * 3 - 1e-9), 1)
        frac = (t * 3) % 1
        size = int(420 * (1.15 - 0.15 * ease(frac * 2)))
        canvas.centered(520, v["attempts"], 110)
        canvas.centered(800 - (size - 420) // 2, str(n), size, p["accent"])
        canvas.centered(1330, v["write_comments"], 72)
        canvas.centered(1420, v["solution_tomorrow"], 46, p["muted"])

    def key(t, k):
        frac = (t * 3) % 1
        return ("countdown", max(3 - int(t * 3 - 1e-9), 1)) if frac >= 0.5 else None

    start = timeline.add(3.0, draw, key)
    for i in range(3):
        timeline.tone(start + i, BEEP, 0.25, 0.45)


def _endcard(timeline: Timeline, theme: Theme, v: dict, duration: float = 3.0, handle: str = "") -> None:
    p = theme.palette

    def draw(canvas, t, k):
        a = ease(t * 2.5)
        canvas.centered(560, v["end_1"], 120)
        canvas.centered(690, v["end_2"], 100, p["accent"])
        canvas.centered(880, v["end_sub"], 50, p["muted"])
        bw, bh = 760, 150
        bx, by = SAFE_CENTER_X - bw // 2, 1080 + int((1 - a) * 80)
        canvas.draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=75, fill=mix(theme.bg_at(by), p["accent"], a))
        canvas.centered(by + 28, v["cta"], 84, (6, 20, 14), max_width=bw - 60)
        canvas.centered(1300, handle, 58)
        canvas.centered(1380, v["link_in_bio"], 50, p["muted"])

    def key(t, k):
        return ("end",) if t * 2.5 >= 1 else None

    start = timeline.add(duration, draw, key)
    timeline.tone(start, FINAL, 0.5, 0.45)


def _per_stop(n: int, nominal: float, fixed: float, limits) -> float:
    return max(0.5, min(nominal, (limits[1] - fixed) / max(n, 1)))


def _stretch(fixed: float, n: int, per_stop: float, limits) -> float:
    """Quanto allungare pausa e chiusura per arrivare alla durata minima."""
    return max(0.0, limits[0] - (fixed + n * per_stop))


# ---------------------------------------------------------------------------------------
# Formati
# ---------------------------------------------------------------------------------------

def _guess_video(fmt: str, card: Card, lang: str, theme: Theme, hook_lines, hook_sub: str,
                 nominal_stop: float, handle: str) -> Timeline:
    v = catalog.video(lang)
    limits = DURATION_LIMITS[fmt]
    rows = Rows(theme, card)
    n = len(card.stops)
    hook, pause, count, end = 2.0, 1.6, 3.0, 3.0
    fixed = hook + pause + count + end
    per_stop = _per_stop(n, nominal_stop, fixed, limits)
    extra = _stretch(fixed, n, per_stop, limits)
    pause += extra / 2
    end += extra / 2

    timeline = Timeline(cover_at=1.2)
    _hook(timeline, theme, hook_lines, hook_sub, hook)
    _reveal(timeline, rows, v["who_title"], v["stop_counter"], per_stop)
    _hold(timeline, rows, v["who_title"], v["last_stop"], pause)
    _countdown(timeline, theme, rows, v)
    _endcard(timeline, theme, v, end, handle)
    return timeline


def who_is(card: Card, lang: str, theme: Theme, handle: str) -> Timeline:
    v = catalog.video(lang)
    n = len(card.stops)
    lines = [line.format(n=n) for line in v["hook_who_is"]]
    return _guess_video("who_is", card, lang, theme, lines, v["hook_who_is_sub"].format(n=n), 1.1, handle)


def percent(card: Card, lang: str, theme: Theme, handle: str) -> Timeline:
    if card.percent_solved is None:
        raise ValueError("il formato percent richiede la percentuale vera (players_count > 0)")
    v = catalog.video(lang)
    n, p = len(card.stops), card.percent_solved
    template = v["hook_percent_low"] if p < 50 else v["hook_percent_high"]
    lines = [line.format(p=p, n=n) for line in template]
    return _guess_video("percent", card, lang, theme, lines, v["hook_percent_sub"].format(n=n), 1.1, handle)


def journeyman(card: Card, lang: str, theme: Theme, handle: str) -> Timeline:
    v = catalog.video(lang)
    n = len(card.stops)
    lines = [line.format(n=n) for line in v["hook_journeyman"]]
    return _guess_video("journeyman", card, lang, theme, lines, v["hook_journeyman_sub"], 0.75, handle)


def ladder(cards: list, lang: str, theme: Theme, handle: str) -> Timeline:
    from promo.picker import LADDER_BANDS

    v = catalog.video(lang)
    p = theme.palette
    timeline = Timeline(cover_at=1.2)
    _hook(timeline, theme, v["hook_ladder"], v["hook_ladder_sub"], 2.0)
    total_levels = len(cards)
    for level, (card, band) in enumerate(zip(cards, LADDER_BANDS), start=1):
        rows = Rows(theme, card, top=ROWS_TOP + 40)
        label = v["ladder_bands"][band]
        counter = v["ladder_level"].format(i=level, n=total_levels)

        def title_card(canvas, t, k, label=label, counter=counter):
            canvas.centered(760, counter, 64, p["muted"])
            canvas.centered(860, label, int(170 * (1.1 - 0.1 * ease(t * 3))), p["accent"])

        start = timeline.add(1.0, title_card, lambda t, k, label=label: ("level", label) if t * 3 >= 1 else None)
        timeline.tone(start, LEVEL, 0.3, 0.4)

        reveal, hold = 2.8, 1.7
        n = len(card.stops)

        def reveal_draw(canvas, t, k, rows=rows, n=n, label=label, counter=counter):
            _header(canvas, label, counter)
            position = t * n
            shown = min(n, int(position) + 1)
            rows.paint(canvas, shown, min(1.0, (position - (shown - 1)) * 2))

        start = timeline.add(reveal, reveal_draw)
        for i in range(n):
            timeline.tone(start + i * reveal / n, TICK, 0.08, 0.3)

        _hold(timeline, rows, label, v["who_title"], hold)

    def outro(canvas, t, k):
        canvas.centered(820, v["ladder_outro"], 110, p["text"], alpha=ease(t * 3))
        canvas.centered(980, v["ladder_outro_sub"], 56, p["muted"], alpha=ease(t * 3))

    timeline.add(1.5, outro, lambda t, k: ("outro",) if t * 3 >= 1 else None)
    used = timeline.total
    _endcard(timeline, theme, v, max(3.0, DURATION_LIMITS["ladder"][0] - used), handle)
    return timeline


def solution(card: Card, lang: str, theme: Theme, handle: str) -> Timeline:
    v = catalog.video(lang)
    p = theme.palette
    rows = Rows(theme, card)
    n = len(card.stops)
    timeline = Timeline()

    build = 2.2

    def intro(canvas, t, k):
        _header(canvas, v["solution_title"], v["solution_sub"])
        position = t * n * 1.2
        shown = min(n, int(position) + 1)
        rows.paint(canvas, shown, min(1.0, position - (shown - 1)))

    start = timeline.add(build, intro)
    for i in range(n):
        timeline.tone(start + i * build / (n * 1.2), TICK, 0.06, 0.25)

    dimmed = _dimmed(theme, rows, 0.82)

    def reveal(canvas, t, k):
        canvas.image.paste(dimmed)
        a = ease(t * 3)
        canvas.centered(640, v["solution_was"], 90, p["muted"], alpha=a)
        size = int(150 * (1.2 - 0.2 * a))
        canvas.centered(760, card.player_name.upper(), size, p["accent"], alpha=a)
        if card.percent_solved is not None:
            canvas.centered(1000, v["solution_percent"].format(p=card.percent_solved), 60, p["text"], alpha=a)

    start = timeline.add(3.8, reveal, lambda t, k: ("reveal",) if t * 3 >= 1 else None)
    timeline.tone(start, CHIME, 0.4, 0.4)
    timeline.tone(start + 0.12, FINAL, 0.5, 0.35)
    timeline.cover_at = start + 1.5
    _endcard(timeline, theme, v, 3.0, handle)
    return timeline


BUILDERS = {
    "who_is": who_is,
    "percent": percent,
    "journeyman": journeyman,
    "solution": solution,
}


def build(fmt: str, cards: list, lang: str, theme: Theme, handle: str) -> Timeline:
    if fmt == "ladder":
        return ladder(cards, lang, theme, handle)
    if fmt not in BUILDERS:
        raise ValueError(f"formato sconosciuto: {fmt!r}")
    return BUILDERS[fmt](cards[0], lang, theme, handle)
