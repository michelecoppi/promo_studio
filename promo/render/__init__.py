"""Generazione dei video: `render()` dalle schede al file MP4 + copertina PNG."""
import os
import time

from promo.config import BOT_USERNAME
from promo.models import RenderResult
from promo.render import engine, formats
from promo.render.engine import Theme


def media_basename(fmt: str, lang: str, cards: list) -> str:
    origin = cards[0].origin if fmt != "ladder" else "ladder-" + "-".join(c.player_id for c in cards)
    return f"{origin}-{fmt}-{lang}"


def render(fmt: str, cards: list, lang: str, theme: Theme, out_dir, *, basename: str = "",
           preset: str = "medium", check: bool = False) -> RenderResult:
    """Scrive `<out_dir>/<basename>.mp4` e `.png`. Stessi input, stesso file."""
    timeline = formats.build(fmt, cards, lang, theme, "@" + BOT_USERNAME)
    if check:
        engine.check_layout(timeline, theme)
    basename = basename or media_basename(fmt, lang, cards)
    os.makedirs(out_dir, exist_ok=True)
    video = os.path.join(str(out_dir), basename + ".mp4")
    cover = os.path.join(str(out_dir), basename + ".png")
    started = time.monotonic()
    digest = engine.encode(timeline, theme, video, cover, preset=preset)
    return RenderResult(
        video_path=video,
        cover_path=cover,
        duration=timeline.frame_count / engine.FPS,
        sha256=digest,
        extra={"seconds": round(time.monotonic() - started, 1)},
    )
