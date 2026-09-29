"""Approvare le bozze da Telegram, con un bot dedicato (`PROMO_APPROVAL_BOT_TOKEN`).

Il workflow non e' un bot sempre acceso, quindi si lavora in due tempi:

1. `ask` (dopo le bozze delle 08:37): ogni video in attesa arriva all'admin
   (`PROMO_ADMIN_CHAT_ID`) con didascalia e due pulsanti, ✅ Approva e ❌ Rifiuta. Un video vale
   per tutti i canali della sua lingua (TikTok e canale Telegram), come in dashboard.
2. `sync` (ogni mezz'ora fino alle 12:45 e prima di pubblicare): legge i pulsanti premuti
   con `getUpdates`, porta i post in `approved`/`rejected` e aggiorna il messaggio.

Il bot e' separato da quello del gioco perche' quello riceve gia' i messaggi dei giocatori via
webhook, e `getUpdates` non funziona su un bot con un webhook attivo. Telegram conserva i
pulsanti premuti per 24 ore: tra le 08:37 e le 12:23 ne passano meno di quattro.

Contano solo i pulsanti premuti dall'admin, nella sua chat: chiunque altro scriva al bot viene
ignorato. Approvare resta un'azione umana (`queue.approve` con il nome di chi ha premuto).
"""
import json
import os
from datetime import datetime
from typing import Optional

import requests

from promo import log, queue
from promo.models import STATUS_APPROVED, STATUS_DRAFT, STATUS_REJECTED
from promo.publishers.base import post_text
from promo.queue import TransitionError
from promo.store import now_iso

API = "https://api.telegram.org"
TIMEOUT = 120
MAX_CAPTION = 1024
APPROVE, REJECT = "ok", "no"
CHANNEL_NAMES = {"tiktok": "TikTok (bozza)", "telegram_channel": "canale Telegram", "x": "X"}


class ApprovalBot:
    def __init__(self, token: str, admin_chat_id: str, session=None):
        self.token = token
        self.admin_chat_id = str(admin_chat_id)
        self.session = session or requests.Session()

    def _call(self, method: str, **kwargs) -> dict:
        response = self.session.post(f"{API}/bot{self.token}/{method}", timeout=TIMEOUT, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {"ok": False, "description": f"HTTP {response.status_code}"}
        if not data.get("ok"):
            raise RuntimeError(log.scrub(f"{method}: {data.get('description') or 'errore sconosciuto'}"))
        return data["result"]

    def send_video(self, path: str, caption: str, keyboard: dict) -> int:
        with open(path, "rb") as fh:
            message = self._call("sendVideo", data={
                "chat_id": self.admin_chat_id, "caption": caption[:MAX_CAPTION], "supports_streaming": "true",
                "reply_markup": json.dumps(keyboard),
            }, files={"video": (os.path.basename(path), fh, "video/mp4")})
        return message["message_id"]

    def send_text(self, text: str) -> None:
        self._call("sendMessage", data={"chat_id": self.admin_chat_id, "text": text[:4096],
                                        "disable_web_page_preview": "true"})

    def updates(self) -> list:
        return self._call("getUpdates", data={"timeout": 0, "allowed_updates": '["callback_query"]'})

    def confirm(self, last_update_id: int) -> None:
        """Segna come letti gli aggiornamenti fino a `last_update_id`: Telegram non li ridara'."""
        self._call("getUpdates", data={"offset": last_update_id + 1, "timeout": 0})

    def answer(self, callback_id: str, text: str) -> None:
        try:
            self._call("answerCallbackQuery", data={"callback_query_id": callback_id, "text": text[:200]})
        except (RuntimeError, requests.RequestException):
            pass  # una risposta tardiva ("query is too old") non e' un errore: conta lo stato in coda

    def edit_caption(self, message_id: int, caption: str) -> None:
        # Senza reply_markup i pulsanti spariscono: una decisione presa non si ripete.
        try:
            self._call("editMessageCaption", data={"chat_id": self.admin_chat_id, "message_id": message_id,
                                                   "caption": caption[:MAX_CAPTION]})
        except (RuntimeError, requests.RequestException) as e:
            log.warning("messaggio di approvazione %s non aggiornato: %s", message_id, e)


def build_bot(settings, session=None) -> Optional[ApprovalBot]:
    if not settings.approval_bot_token or not settings.admin_chat_id:
        return None
    return ApprovalBot(settings.approval_bot_token, settings.admin_chat_id, session)


def _groups(posts: list) -> list:
    """I post che condividono lo stesso video (stessa lingua, stesso file), in ordine stabile."""
    groups = {}
    for post in sorted(posts, key=lambda p: p["id"]):
        key = (post.get("created_for"), post.get("language"), post.get("media_sha256") or post["id"])
        groups.setdefault(key, []).append(post)
    return list(groups.values())


def summary(posts: list) -> str:
    first = next((p for p in posts if p.get("channel") == "telegram_channel"), posts[0])
    channels = ", ".join(CHANNEL_NAMES.get(p["channel"], p["channel"]) for p in posts)
    head = (f"🎬 {first.get('format')} · {first.get('language')} · {first.get('created_for')}\n"
            f"Canali: {channels}")
    return f"{head}\n\n{post_text(first)}"


def _decision_line(status: str, actor: str) -> str:
    if status == STATUS_APPROVED:
        return f"✅ Approvato da {actor}: esce alle 12:00."
    return f"❌ Rifiutato da {actor}."


def ask(store, bot: ApprovalBot, theme, settings, *, day: Optional[str] = None,
        now: Optional[datetime] = None) -> list:
    """Manda all'admin i video in bozza non ancora inviati e non ancora scaduti (o quelli di
    `day`). Rilanciarlo non manda doppioni."""
    from promo import plan

    now_s = now_iso(now)
    drafts = [p for p in store.list(STATUS_DRAFT) if not p.get("approval_message_id") and (
        p.get("created_for") == day if day else (p.get("scheduled_for") or "") > now_s)]
    lines = []
    for posts in _groups(drafts):
        ids = [p["id"] for p in posts]
        try:
            media = plan.ensure_media(posts[0], theme, settings.media_dir)
            keyboard = {"inline_keyboard": [[{"text": "✅ Approva", "callback_data": APPROVE},
                                             {"text": "❌ Rifiuta", "callback_data": REJECT}]]}
            message_id = bot.send_video(media, summary(posts), keyboard)
        except Exception as e:
            lines.append(f"{', '.join(ids)}: invio all'admin fallito ({log.scrub(e)})")
            continue
        for post in posts:
            store.update(post["id"], {"approval_message_id": message_id})
        lines.append(f"{', '.join(ids)}: inviato all'admin per l'approvazione")
    return lines or ["nessuna bozza da inviare"]


def _actor(settings, user: dict) -> str:
    if settings.admin_name:
        return settings.admin_name
    return user.get("username") or user.get("first_name") or f"telegram:{user.get('id')}"


def sync(store, bot: ApprovalBot, settings) -> list:
    """Applica i pulsanti premuti dall'admin. Idempotente: un post gia' deciso non cambia."""
    updates = bot.updates()
    if not updates:
        return ["nessuna risposta dall'admin"]
    lines = []
    for update in updates:
        callback = update.get("callback_query")
        if not callback:
            continue
        user = callback.get("from") or {}
        message = callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if str(user.get("id")) != bot.admin_chat_id or chat_id != bot.admin_chat_id:
            lines.append(f"pulsante ignorato: non viene dall'admin (utente {user.get('id')})")
            continue
        choice = callback.get("data")
        message_id = message.get("message_id")
        posts = [p for p in store.list() if p.get("approval_message_id") == message_id]
        if choice not in (APPROVE, REJECT) or not posts:
            bot.answer(callback["id"], "Post non trovato")
            continue
        actor = _actor(settings, user)
        target = STATUS_APPROVED if choice == APPROVE else STATUS_REJECTED
        done = []
        for post in posts:
            if post["status"] != STATUS_DRAFT:
                lines.append(f"{post['id']}: gia' {post['status']}, lasciato com'e'")
                continue
            try:
                if target == STATUS_APPROVED:
                    queue.approve(store, post["id"], actor)
                else:
                    queue.reject(store, post["id"], actor, reason="rifiutato da Telegram")
            except TransitionError as e:
                lines.append(f"{post['id']}: {e}")
                continue
            done.append(post["id"])
            lines.append(f"{post['id']}: {target} da {actor} (Telegram)")
        decided = store.get(posts[0]["id"]) or posts[0]
        bot.answer(callback["id"], "Approvato" if decided["status"] == STATUS_APPROVED else
                   "Rifiutato" if decided["status"] == STATUS_REJECTED else decided["status"])
        if done:
            bot.edit_caption(message_id, f"{summary(posts)}\n\n{_decision_line(target, actor)}")
    bot.confirm(max(u["update_id"] for u in updates))
    return lines or ["nessuna decisione nuova"]


def report_published(bot: ApprovalBot, lines: list) -> None:
    """Dopo la pubblicazione, l'esito all'admin (solo se e' successo qualcosa)."""
    if not lines or lines == ["niente da pubblicare"]:
        return
    try:
        bot.send_text("📣 Pubblicazione delle 12:23\n\n" + "\n".join(lines))
    except (RuntimeError, requests.RequestException) as e:
        log.warning("esito della pubblicazione non inviato all'admin: %s", e)

