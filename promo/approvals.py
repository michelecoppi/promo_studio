"""Approvare le bozze da Telegram, con un bot dedicato (`PROMO_APPROVAL_BOT_TOKEN`).

Il workflow non e' un bot sempre acceso, quindi si lavora in due tempi:

1. `ask` (dopo le bozze delle 08:37): ogni video in attesa arriva all'admin
   (`PROMO_ADMIN_CHAT_ID`) con didascalia e due pulsanti, ✅ Approva e ❌ Rifiuta. Un video vale
   per tutti i canali della sua lingua (TikTok e canale Telegram), come in dashboard.
2. il pulsante premuto porta i post in `approved`/`rejected` e aggiorna il messaggio
   (`handle_press`). Arriva in due modi:
   - **webhook** (promo/approval_service.py, su Cloud Run): Telegram lo chiama appena l'admin
     preme, e la decisione e' immediata;
   - **`sync`** (ogni mezz'ora fino alle 12:45 e prima di pubblicare): legge i pulsanti con
     `getUpdates`. Serve solo senza webhook: con il webhook attivo Telegram rifiuta
     `getUpdates`, e `sync` si fa da parte.

Il bot e' separato da quello del gioco perche' quello riceve gia' i messaggi dei giocatori sul
suo webhook. Telegram conserva i pulsanti premuti per 24 ore: tra le 08:37 e le 12:23 ne
passano meno di quattro.

Contano solo i pulsanti premuti dall'admin, nella sua chat: chiunque altro scriva al bot viene
ignorato. Approvare resta un'azione umana (`queue.approve` con il nome di chi ha premuto).

Lo stesso bot porta anche i brief del supervisore (promo/supervisor_briefs.py) con ✅ Usa / ❌ Scarta:
i loro pulsanti hanno il prefisso `brief:` e `handle_press` li passa a quel modulo, con gli stessi
controlli sull'admin.
"""
import json
import os
from datetime import datetime
from typing import Optional

import requests

from promo import log, queue, supervisor_briefs
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

    def send_message(self, text: str, keyboard: dict) -> int:
        """Messaggio di testo semplice (niente parse_mode) con pulsanti; ritorna il suo id."""
        message = self._call("sendMessage", data={"chat_id": self.admin_chat_id, "text": text[:4096],
                                                  "disable_web_page_preview": "true",
                                                  "reply_markup": json.dumps(keyboard)})
        return message["message_id"]

    def edit_text(self, message_id: int, text: str) -> None:
        # Senza reply_markup i pulsanti spariscono: una decisione presa non si ripete.
        try:
            self._call("editMessageText", data={"chat_id": self.admin_chat_id, "message_id": message_id,
                                                "text": text[:4096], "disable_web_page_preview": "true"})
        except (RuntimeError, requests.RequestException) as e:
            log.warning("messaggio %s non aggiornato: %s", message_id, e)

    def send_text(self, text: str) -> None:
        self._call("sendMessage", data={"chat_id": self.admin_chat_id, "text": text[:4096],
                                        "disable_web_page_preview": "true"})

    def updates(self) -> list:
        return self._call("getUpdates", data={"timeout": 0, "allowed_updates": '["callback_query"]'})

    def confirm(self, last_update_id: int) -> None:
        """Segna come letti gli aggiornamenti fino a `last_update_id`: Telegram non li ridara'."""
        self._call("getUpdates", data={"offset": last_update_id + 1, "timeout": 0})

    def webhook_url(self) -> str:
        return (self._call("getWebhookInfo") or {}).get("url") or ""

    def set_webhook(self, url: str, secret: str) -> None:
        # Solo i pulsanti: i messaggi scritti al bot non servono a nessuno.
        self._call("setWebhook", data={"url": url, "secret_token": secret,
                                       "allowed_updates": '["callback_query"]', "max_connections": "5"})

    def delete_webhook(self) -> None:
        self._call("deleteWebhook")

    def answer(self, callback_id: str, text: str) -> None:
        try:
            self._call("answerCallbackQuery", data={"callback_query_id": callback_id, "text": text[:200]})
        except (RuntimeError, requests.RequestException) as e:
            # Una risposta tardiva ("query is too old") non e' grave: conta lo stato in coda.
            log.warning("risposta al pulsante non inviata: %s", e)

    def close(self, message_id: int, note: str) -> None:
        """Toglie i pulsanti da un messaggio che non porta piu' a niente, con una riga di spiegazione."""
        try:
            self._call("editMessageReplyMarkup", data={"chat_id": self.admin_chat_id, "message_id": message_id,
                                                       "reply_markup": json.dumps({"inline_keyboard": []})})
            self.send_text(note)
        except (RuntimeError, requests.RequestException) as e:
            log.warning("pulsanti del messaggio %s non tolti: %s", message_id, e)

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
        return f"✅ Approvato da {actor}: esce alle 12:23."
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


def handle_press(store, bot: ApprovalBot, settings, callback: dict, decisions=None) -> list:
    """Un pulsante premuto (`callback_query`). Idempotente: un post gia' deciso non cambia.

    I pulsanti dei brief del supervisore (`brief:...`) vanno a `supervisor_briefs.handle_press`,
    con le decisioni in `decisions` (collezione `promo_brief_decisions`)."""
    user = callback.get("from") or {}
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if str(user.get("id")) != bot.admin_chat_id or chat_id != bot.admin_chat_id:
        return [f"pulsante ignorato: non viene dall'admin (utente {user.get('id')})"]
    choice = callback.get("data")
    if supervisor_briefs.is_brief_press(choice):
        return supervisor_briefs.handle_press(decisions, bot, callback, _actor(settings, user))
    message_id = message.get("message_id")
    posts = [p for p in store.list() if p.get("approval_message_id") == message_id]
    if choice not in (APPROVE, REJECT) or not posts:
        bot.answer(callback["id"], "Post non trovato")
        if not posts:  # bozze cancellate o rigenerate: i pulsanti non servono piu'
            bot.close(message_id, "⚠️ Quelle bozze non sono più in coda: i pulsanti sono stati tolti. "
                                  "Usa il messaggio più recente.")
        return [f"pulsante sul messaggio {message_id}: nessun post"]
    actor = _actor(settings, user)
    target = STATUS_APPROVED if choice == APPROVE else STATUS_REJECTED
    lines, done = [], []
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
    return lines


def sync(store, bot: ApprovalBot, settings, decisions=None) -> list:
    """Applica i pulsanti premuti dall'admin e non ancora letti (solo senza webhook)."""
    if bot.webhook_url():
        return ["webhook attivo: i pulsanti si applicano appena premuti, niente da leggere"]
    updates = bot.updates()
    if not updates:
        return ["nessuna risposta dall'admin"]
    lines = []
    for update in updates:
        callback = update.get("callback_query")
        if callback:
            lines.extend(handle_press(store, bot, settings, callback, decisions))
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

