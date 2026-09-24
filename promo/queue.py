"""La macchina a stati dei post e le azioni dell'admin (§5.4).

```
draft ──→ approved ──→ published
  │          │  └────→ failed ──→ published   (riprovabile)
  └──→ rejected ←──────┴──────────┘
```

- Un post nasce `draft` e diventa `approved` **solo per azione di una persona**: approvare,
  rifiutare e modificare i testi richiedono un `actor` che non sia un attore di sistema.
- Solo il publisher porta un post a `published` o `failed`, e solo da `approved`/`failed`.
- Ogni cambio di stato aggiunge una voce a `history` (chi, quando, da, a) oltre ai campi di
  audit della specifica (`approved_at`, `approved_by`, `published_at`).
"""
from datetime import datetime
from typing import Optional

from promo.models import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_REJECTED,
)
from promo.store import NotFound, PostStore, now_iso

SYSTEM_ACTORS = frozenset({"publisher", "scheduler", "system", "bot", "workflow"})

TRANSITIONS = {
    STATUS_DRAFT: {STATUS_APPROVED, STATUS_REJECTED},
    STATUS_APPROVED: {STATUS_PUBLISHED, STATUS_FAILED, STATUS_REJECTED},
    STATUS_FAILED: {STATUS_PUBLISHED, STATUS_FAILED, STATUS_REJECTED},
    STATUS_REJECTED: set(),
    STATUS_PUBLISHED: set(),
}

HUMAN_ONLY = {STATUS_APPROVED, STATUS_REJECTED}
PUBLISHER_ONLY = {STATUS_PUBLISHED, STATUS_FAILED}

EDITABLE = {STATUS_DRAFT, STATUS_APPROVED, STATUS_FAILED}
MAX_CAPTION = 150


class TransitionError(ValueError):
    pass


def _is_human(actor: str) -> bool:
    return bool(actor and actor.strip()) and actor.strip().lower() not in SYSTEM_ACTORS


def check_transition(current: str, target: str, actor: str) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise TransitionError(f"transizione non ammessa: {current} → {target}")
    if target in HUMAN_ONLY and not _is_human(actor):
        raise TransitionError(f"{target} richiede un'azione umana (actor={actor!r})")
    if target in PUBLISHER_ONLY and actor != "publisher":
        raise TransitionError(f"{target} lo imposta solo il publisher")


def _entry(current: str, target: str, actor: str, at: str, note: str = "") -> dict:
    entry = {"from": current, "to": target, "by": actor, "at": at}
    if note:
        entry["note"] = note
    return entry


def transition(store: PostStore, post_id: str, target: str, actor: str, *, now: Optional[datetime] = None,
               note: str = "", fields: Optional[dict] = None) -> dict:
    post = store.get(post_id)
    if not post:
        raise NotFound(post_id)
    current = post["status"]
    check_transition(current, target, actor)
    at = now_iso(now)
    update = dict(fields or {})
    update["status"] = target
    update["history"] = list(post.get("history") or []) + [_entry(current, target, actor, at, note)]
    if target == STATUS_APPROVED:
        update.update(approved_at=at, approved_by=actor)
    elif target == STATUS_REJECTED:
        update.update(rejected_at=at, rejected_by=actor)
    elif target == STATUS_PUBLISHED:
        update.update(published_at=at, error="")
    # Solo il post nello stato atteso: se nel frattempo e' cambiato, non si sovrascrive.
    result = store.claim(post_id, lambda p: p["status"] == current, update)
    if result is None:
        raise TransitionError(f"{post_id} e' cambiato nel frattempo: riprova")
    return result


def approve(store: PostStore, post_id: str, actor: str, *, now: Optional[datetime] = None) -> dict:
    return transition(store, post_id, STATUS_APPROVED, actor, now=now)


def reject(store: PostStore, post_id: str, actor: str, *, reason: str = "", now: Optional[datetime] = None) -> dict:
    return transition(store, post_id, STATUS_REJECTED, actor, now=now, note=reason)


def edit_copy(store: PostStore, post_id: str, actor: str, *, caption: Optional[str] = None,
              hashtags: Optional[list] = None, pinned_comment: Optional[str] = None,
              now: Optional[datetime] = None) -> dict:
    if not _is_human(actor):
        raise TransitionError("solo una persona puo' modificare i testi")
    post = store.get(post_id)
    if not post:
        raise NotFound(post_id)
    if post["status"] not in EDITABLE:
        raise TransitionError(f"un post {post['status']} non si modifica piu'")
    fields = {}
    if caption is not None:
        caption = " ".join(caption.split())
        if not caption or len(caption) > MAX_CAPTION:
            raise ValueError(f"la didascalia deve avere 1-{MAX_CAPTION} caratteri (ne ha {len(caption)})")
        fields["caption"] = caption
    if hashtags is not None:
        tags = [t if t.startswith("#") else "#" + t for t in (h.strip() for h in hashtags) if t]
        if not 3 <= len(tags) <= 5:
            raise ValueError("servono da 3 a 5 hashtag")
        fields["hashtags"] = tags
    if pinned_comment is not None:
        fields["pinned_comment"] = pinned_comment.strip()
    if not fields:
        return post
    changed = ", ".join(sorted(fields))
    at = now_iso(now)
    fields["edited_at"] = at
    fields["edited_by"] = actor
    fields["history"] = list(post.get("history") or []) + [
        _entry(post["status"], post["status"], actor, at, "testi modificati: " + changed)
    ]
    return store.update(post_id, fields)
