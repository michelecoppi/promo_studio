"""Canale Telegram del gioco (§5.5): `sendVideo` con il bot, **solo** sul canale configurato.

Regole:
- la chat di destinazione e' sempre `PROMO_TELEGRAM_CHANNEL_ID`, mai un valore del post;
- prima del primo invio si verifica con `getChat` che sia davvero un **canale** (tipo
  `channel`): un gruppo, anche se il bot ne fa parte, viene rifiutato. Niente gruppi di terzi.
- il token sta solo nell'URL della Bot API e non esce mai nei messaggi d'errore (log.scrub).
"""
import os

import requests

from promo import log
from promo.publishers.base import Publisher, PublishResult, post_text

API = "https://api.telegram.org"
TIMEOUT = 120
MAX_CAPTION = 1024  # limite della Bot API per le didascalie dei media


class TelegramChannelPublisher(Publisher):
    channel = "telegram_channel"

    def __init__(self, bot_token: str, channel_id: str, session=None):
        self.token = bot_token
        self.channel_id = channel_id
        self.session = session or requests.Session()
        self._verified = None

    def _call(self, method: str, **kwargs) -> dict:
        response = self.session.post(f"{API}/bot{self.token}/{method}", timeout=TIMEOUT, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {"ok": False, "description": f"HTTP {response.status_code}"}
        if not data.get("ok"):
            raise RuntimeError(f"{method}: {data.get('description') or 'errore sconosciuto'}")
        return data["result"]

    def _verify_channel(self) -> dict:
        if self._verified is None:
            chat = self._call("getChat", data={"chat_id": self.channel_id})
            if chat.get("type") != "channel":
                raise PermissionError(
                    f"PROMO_TELEGRAM_CHANNEL_ID indica una chat di tipo {chat.get('type')!r}: "
                    "si pubblica solo su un canale di proprieta'"
                )
            self._verified = chat
        return self._verified

    def describe(self, post, media_path):
        return (f"sendVideo su {self.channel_id or '(canale non configurato)'} con {os.path.basename(media_path)} "
                f"e {len(post_text(post))} caratteri di testo")

    def publish(self, post, media_path) -> PublishResult:
        if not self.token or not self.channel_id:
            return PublishResult.failure("BOT_TOKEN o PROMO_TELEGRAM_CHANNEL_ID non configurati")
        try:
            chat = self._verify_channel()
            text = post_text(post)[:MAX_CAPTION]
            files = {"video": (os.path.basename(media_path), open(media_path, "rb"), "video/mp4")}
            cover = post.get("cover_path")
            if cover and os.path.exists(cover):
                files["thumbnail"] = (os.path.basename(cover), open(cover, "rb"), "image/png")
            try:
                message = self._call("sendVideo", data={
                    "chat_id": self.channel_id,
                    "caption": text,
                    "supports_streaming": "true",
                    "width": 1080,
                    "height": 1920,
                    "duration": int(round(post.get("duration") or 0)) or None,
                }, files=files)
            finally:
                for _, handle, _ in files.values():
                    handle.close()
        except (requests.RequestException, RuntimeError, PermissionError, OSError) as e:
            return PublishResult.failure(log.scrub(e))
        message_id = message.get("message_id")
        return PublishResult(ok=True, external_id=f"{self.channel_id}:{message_id}",
                             external_url=_message_url(chat, message_id))


def _message_url(chat: dict, message_id) -> str:
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{message_id}"
    chat_id = str(chat.get("id", ""))
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return ""
