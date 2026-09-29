"""Brief del supervisore letti dal suo Firestore, con ✅ Usa / ❌ Scarta sul bot approvazioni (#7).

Il supervisore (gtp_orchestrator, issue #9) pubblica ogni brief nel proprio Firestore, in
`promo_briefs/{campaign_id}`, con `status: proposed`, `schema_version`, `expires_at` e `brief`, che e'
esattamente il JSON di `brief-import`. Esempio: tests/fixtures/supervisor_promo_brief.json.

Promo lo legge **in sola lettura** (progetto `PROMO_SUPERVISOR_FIRESTORE_PROJECT`, vuoto = funzione
spenta) e lavora in tre tempi, tutti idempotenti:

1. `brief-ask` (dopo le bozze delle 08:37): ogni brief nuovo, valido e non scaduto arriva all'admin con
   ✅ Usa / ❌ Scarta. Prima dell'invio si crea `promo_brief_decisions/{campaign_id}` (`asking`): un brief
   gia' presente non si ripropone. Invio riuscito → `asked` con `message_id`; fallito → `send_failed`, e
   si ritenta al giro dopo. Uno rimasto `asking` (crash durante l'invio) non si ripete alla cieca.
2. il pulsante (`brief:use:<campaign_id>` / `brief:skip:<campaign_id>`) arriva allo stesso webhook dei
   post (approval_service → approvals.handle_press, che instrada per prefisso): solo l'admin, nella sua
   chat, e solo una volta (`claim` atomico da `asked`): un secondo tocco non cambia la decisione.
3. `brief-apply` (al giro delle bozze): i brief `used` non ancora importati diventano bozze con
   `briefs.import_brief`, le stesse regole di `brief-import`; poi si approvano come tutti i post.

Il supervisore non scrive mai qui e Promo non scrive mai nel Firestore del supervisore: le decisioni
restano nel Firestore del gioco, dove il supervisore le legge (collector Promo).

Il modulo e' importato anche dal webhook su Cloud Run, che non ha Pillow ne' il gioco: `promo.briefs`
(che porta con se' il rendering) si importa solo dentro le funzioni che lo usano.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol

from promo import log
from promo.store import FirestoreStore, JsonFileStore, PostStore, now_iso

SUPERVISOR_COLLECTION = "promo_briefs"
DECISIONS = "promo_brief_decisions"
SCHEMA_VERSIONS = (1,)
PREFIX = "brief:"
USE, SKIP = "use", "skip"

ASKING, ASKED, SEND_FAILED = "asking", "asked", "send_failed"
USED, DISCARDED = "used", "discarded"
CHANNEL_NAMES = {"tiktok": "TikTok (bozza)", "telegram_channel": "canale Telegram", "x": "X"}
MAX_TEXT = 4096
LABELS = {USED: "usato", DISCARDED: "scartato", ASKING: "in invio", SEND_FAILED: "invio fallito"}


class BriefSource(Protocol):
    def proposed(self) -> list: ...


class FirestoreBriefSource:
    """Il Firestore del supervisore, con l'identita' del lavoro (ADC, Workload Identity in Actions).
    Serve `roles/datastore.viewer` sul progetto del supervisore: nessuna scrittura."""

    def __init__(self, project: str, client=None):
        self.project = project
        self._client = client

    def proposed(self) -> list:
        db = self._client
        if db is None:
            from google.cloud import firestore
            db = firestore.Client(project=self.project)
        query = db.collection(SUPERVISOR_COLLECTION).where("status", "==", "proposed")
        return [doc.to_dict() for doc in query.stream()]


class StaticBriefSource:
    def __init__(self, docs: list):
        self.docs = docs

    def proposed(self) -> list:
        return [dict(d) for d in self.docs if d.get("status") == "proposed"]


def build_source(settings) -> Optional[FirestoreBriefSource]:
    project = settings.supervisor_firestore_project
    return FirestoreBriefSource(project) if project else None


def decisions_for(store) -> Optional[PostStore]:
    """Le decisioni accanto alla coda: stesso Firestore (collezione `promo_brief_decisions`) o, in
    locale, un file accanto a `promo_posts.json`. None se la coda non e' di questi tipi."""
    if isinstance(store, FirestoreStore):
        return FirestoreStore(store.db, DECISIONS)
    if isinstance(store, JsonFileStore):
        return JsonFileStore(Path(store.path).with_name(f"{DECISIONS}.json"))
    return None


# --- 1. lettura e proposta all'admin ------------------------------------------------------------
def pending(source: BriefSource, now: Optional[datetime] = None) -> tuple:
    """(brief validi da proporre, righe per i brief scartati perche' scaduti o non validi)."""
    from promo import briefs

    now_s = now_iso(now)
    ready, lines = [], []
    for doc in sorted(source.proposed(), key=lambda d: (d.get("created_at") or "", d.get("campaign_id") or "")):
        cid = doc.get("campaign_id") or "?"
        if doc.get("schema_version") not in SCHEMA_VERSIONS:
            lines.append(f"{cid}: schema_version {doc.get('schema_version')!r} non supportata, ignorato")
            continue
        if not doc.get("expires_at") or doc["expires_at"] <= now_s:
            continue  # scaduto: non si propone piu'
        try:
            brief = briefs.validate(doc.get("brief"))
        except briefs.BriefError as e:
            lines.append(f"{cid}: brief non valido ({e}), ignorato")
            continue
        if brief["campaign_id"] != cid:
            lines.append(f"{cid}: campaign_id diverso nel brief ({brief['campaign_id']}), ignorato")
            continue
        ready.append({"doc": doc, "brief": brief})
    return ready, lines


def message(doc: dict, brief: dict) -> str:
    """Testo semplice (niente parse_mode): CTA, angolo e fatti vengono da un modello e non si interpretano."""
    facts = "\n".join(f"• {f}" for f in brief["facts"]) or "• (nessuno)"
    return (
        f"🧭 Brief del supervisore · {brief['campaign_id']}\n"
        f"Settimana {doc.get('week') or '?'} · proponibile fino al {(doc.get('expires_at') or '?')[:10]}\n"
        f"{CHANNEL_NAMES.get(brief['channel'], brief['channel'])} · {brief['language']} · {brief['format']}\n\n"
        f"CTA: {brief['cta'] or '-'}\n"
        f"Angolo: {brief['angle'] or '-'}\n"
        f"Fatti:\n{facts}\n\n"
        "✅ Usa: le bozze nascono al prossimo giro delle 08:37 e si approvano come sempre.\n"
        "❌ Scarta: il brief non verra' riproposto."
    )[:MAX_TEXT]


def keyboard(campaign_id: str) -> dict:
    return {"inline_keyboard": [[{"text": "✅ Usa", "callback_data": f"{PREFIX}{USE}:{campaign_id}"},
                                 {"text": "❌ Scarta", "callback_data": f"{PREFIX}{SKIP}:{campaign_id}"}]]}


def ask(source: BriefSource, decisions: PostStore, bot, *, dry_run: bool = False,
        now: Optional[datetime] = None) -> list:
    """Manda all'admin i brief nuovi. Rilanciarlo non manda doppioni."""
    ready, lines = pending(source, now)
    at = now_iso(now)
    for item in ready:
        doc, brief = item["doc"], item["brief"]
        cid = brief["campaign_id"]
        current = decisions.get(cid)
        if current and current.get("status") != SEND_FAILED:
            continue  # gia' proposto (o deciso): mai due volte
        if dry_run:
            lines.append(f"{cid}: (dry-run) da proporre all'admin")
            continue
        fields = {"status": ASKING, "asked_at": at, "brief": brief, "week": doc.get("week"),
                  "supervisor_expires_at": doc.get("expires_at"), "message_id": None}
        if current:  # ritento di un invio fallito, solo se nessun altro l'ha gia' preso
            if decisions.claim(cid, lambda d: d.get("status") == SEND_FAILED, fields) is None:
                continue
        elif not decisions.create({"id": cid, "campaign_id": cid, **fields}):
            continue
        try:
            message_id = bot.send_message(message(doc, brief), keyboard(cid))
        except Exception as e:
            decisions.update(cid, {"status": SEND_FAILED, "error": log.scrub(e)[:300]})
            lines.append(f"{cid}: invio all'admin fallito ({log.scrub(e)}), si ritenta al prossimo giro")
            continue
        decisions.update(cid, {"status": ASKED, "message_id": message_id})
        lines.append(f"{cid}: proposto all'admin (✅ Usa / ❌ Scarta)")
    return lines or ["nessun brief nuovo del supervisore"]


# --- 2. il pulsante ---------------------------------------------------------------------------------
def is_brief_press(data) -> bool:
    return isinstance(data, str) and data.startswith(PREFIX)


def _decision_line(status: str, actor: str) -> str:
    if status == USED:
        return f"✅ Usato da {actor}: le bozze nascono al prossimo giro delle 08:37."
    return f"❌ Scartato da {actor}: non verra' riproposto."


def handle_press(decisions: Optional[PostStore], bot, callback: dict, actor: str) -> list:
    """Un pulsante ✅ Usa / ❌ Scarta, gia' verificato come premuto dall'admin nella sua chat.
    Idempotente: una decisione presa non cambia."""
    parts = (callback.get("data") or "").split(":", 2)
    message_id = (callback.get("message") or {}).get("message_id")
    if len(parts) != 3 or parts[1] not in (USE, SKIP) or not parts[2]:
        bot.answer(callback["id"], "Pulsante non valido")
        return [f"pulsante brief non valido: {callback.get('data')!r}"]
    choice, cid = parts[1], parts[2]
    if decisions is None:
        bot.answer(callback["id"], "Brief non gestiti qui")
        return [f"{cid}: decisioni sui brief non configurate"]
    current = decisions.get(cid)
    if not current:
        bot.answer(callback["id"], "Brief non trovato")
        return [f"{cid}: brief non trovato"]
    target = USED if choice == USE else DISCARDED

    def undecided(doc: dict) -> bool:
        # `asking`: invio riuscito ma message_id non salvato (crash): il pulsante vale lo stesso.
        return doc.get("status") in (ASKING, ASKED) and doc.get("message_id") in (None, message_id)

    decided = decisions.claim(cid, undecided, {"status": target, "decided_by": actor, "decided_at": now_iso(),
                                               "message_id": message_id})
    if decided is None:
        status = (decisions.get(cid) or current).get("status")
        bot.answer(callback["id"], f"Gia' deciso: {LABELS.get(status, status)}")
        return [f"{cid}: gia' {status}, lasciato com'e'"]
    bot.answer(callback["id"], "Brief usato" if target == USED else "Brief scartato")
    bot.edit_text(message_id, f"{message(_doc_view(decided), decided['brief'])}\n\n{_decision_line(target, actor)}")
    return [f"{cid}: {target} da {actor} (Telegram)"]


def _doc_view(decision: dict) -> dict:
    return {"week": decision.get("week"), "expires_at": decision.get("supervisor_expires_at")}


# --- 3. dai brief usati alle bozze --------------------------------------------------------------
def apply_used(settings, game, store: PostStore, theme, decisions: PostStore, *, dry_run: bool = False,
               now: Optional[datetime] = None) -> list:
    """I brief `used` non ancora importati diventano bozze (stessa logica di `brief-import`)."""
    from promo import briefs

    lines = []
    for decision in sorted(decisions.list(USED), key=lambda d: d["id"]):
        if decision.get("imported_at"):
            continue
        try:
            result = briefs.import_brief(settings, game, store, theme, decision["brief"], dry_run=dry_run, now=now)
        except Exception as e:  # un brief che non si puo' importare non ferma gli altri
            lines.append(f"{decision['id']}: bozze non create ({log.scrub(e)})")
            if not dry_run:
                decisions.update(decision["id"], {"import_error": log.scrub(e)[:300]})
            continue
        lines.extend(result)
        if not dry_run:
            day = decision["brief"].get("day") or game.today()
            decisions.update(decision["id"], {"imported_at": now_iso(now), "imported_for": day,
                                              "import_error": None})
    return lines or ["nessun brief del supervisore da trasformare in bozze"]
