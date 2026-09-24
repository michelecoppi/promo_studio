from datetime import datetime, timezone
from pathlib import Path

from promo.config import Settings
from promo.models import RenderResult

NOW = datetime(2026, 9, 24, 10, 30, tzinfo=timezone.utc)  # 12:30 a Roma


def settings(tmp_path, **kw):
    base = dict(enabled=True, media_dir=Path(tmp_path) / "media", telegram_channel_id="@promo_channel",
                bot_token="123456:SECRET-token-value", telegram_languages=("it",))
    base.update(kw)
    return Settings(**base)


def fake_render(calls=None):
    """Sostituisce promo.render.render: scrive file finti, niente ffmpeg."""
    def _render(fmt, cards, lang, theme, out_dir, *, basename="", preset="medium", check=False):
        from promo.render import media_basename
        basename = basename or media_basename(fmt, lang, cards)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        video, cover = out / f"{basename}.mp4", out / f"{basename}.png"
        video.write_bytes(b"video:" + basename.encode())
        cover.write_bytes(b"png")
        if calls is not None:
            calls.append((fmt, lang, [c.player_id for c in cards]))
        return RenderResult(str(video), str(cover), 20.0, "sha-" + basename)
    return _render
