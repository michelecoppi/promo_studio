"""La coda `promo_posts`: Firestore in produzione, un file JSON in locale, memoria nei test.

Tre implementazioni con la stessa interfaccia. Quella che conta per la sicurezza e'
`claim()`: un confronto-e-scrivi atomico (una transazione su Firestore) con cui il
publisher "prende" un post prima di chiamare il canale. Due esecuzioni sovrapposte del
workflow non possono quindi pubblicare lo stesso post due volte.

Le date sono stringhe ISO 8601 in UTC (`2026-09-24T10:00:00Z`): ordinabili come stringhe,
identiche nel file JSON e su Firestore.
"""
import copy
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Optional, Protocol

COLLECTION = "promo_posts"


def now_iso(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PostStore(Protocol):
    def get(self, post_id: str) -> Optional[dict]: ...

    def create(self, post: dict) -> bool: ...

    def update(self, post_id: str, fields: dict) -> dict: ...

    def list(self, status: Optional[str] = None) -> list: ...

    def claim(self, post_id: str, check: Callable[[dict], bool], fields: dict) -> Optional[dict]: ...


class NotFound(KeyError):
    pass


class MemoryStore:
    def __init__(self, posts: Optional[dict] = None):
        self.posts = copy.deepcopy(posts or {})
        self._lock = Lock()

    def _saved(self) -> None:
        pass

    def get(self, post_id):
        post = self.posts.get(post_id)
        return copy.deepcopy(post) if post else None

    def create(self, post):
        """Crea solo se l'id non esiste: rigenerare le bozze e' idempotente."""
        with self._lock:
            if post["id"] in self.posts:
                return False
            self.posts[post["id"]] = copy.deepcopy(post)
            self._saved()
            return True

    def update(self, post_id, fields):
        with self._lock:
            if post_id not in self.posts:
                raise NotFound(post_id)
            self.posts[post_id].update(copy.deepcopy(fields))
            self._saved()
            return copy.deepcopy(self.posts[post_id])

    def list(self, status=None):
        return [copy.deepcopy(p) for p in self.posts.values() if status is None or p.get("status") == status]

    def claim(self, post_id, check, fields):
        with self._lock:
            post = self.posts.get(post_id)
            if not post or not check(copy.deepcopy(post)):
                return None
            post.update(copy.deepcopy(fields))
            self._saved()
            return copy.deepcopy(post)


class JsonFileStore(MemoryStore):
    """Coda su file, per chi lavora senza Firestore (PROMO_STORE=local)."""

    def __init__(self, path):
        self.path = Path(path)
        posts = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as fh:
                posts = json.load(fh)
        super().__init__(posts)

    def _saved(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.posts, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)


class FirestoreStore:
    def __init__(self, db, collection: str = COLLECTION):
        self.db = db
        self.collection = collection

    def _ref(self, post_id):
        return self.db.collection(self.collection).document(post_id)

    def get(self, post_id):
        snapshot = self._ref(post_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def create(self, post):
        from google.api_core.exceptions import AlreadyExists
        try:
            self._ref(post["id"]).create(post)
            return True
        except AlreadyExists:
            return False

    def update(self, post_id, fields):
        ref = self._ref(post_id)
        ref.update(fields)
        return ref.get().to_dict()

    def list(self, status=None):
        query = self.db.collection(self.collection)
        if status:
            query = query.where("status", "==", status)
        return [doc.to_dict() for doc in query.stream()]

    def claim(self, post_id, check, fields):
        from firebase_admin import firestore

        ref = self._ref(post_id)

        @firestore.transactional
        def _claim(transaction):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            post = snapshot.to_dict()
            if not check(post):
                return None
            transaction.update(ref, fields)
            post.update(fields)
            return post

        return _claim(self.db.transaction())


def open_store(settings, game_source=None) -> PostStore:
    if settings.store == "local":
        return JsonFileStore(settings.local_store_path)
    if settings.store == "firestore":
        if game_source is None:
            from promo import game
            game_source = game.default(settings.game_repo_path)
        return FirestoreStore(game_source.firestore_db())
    raise ValueError(f"PROMO_STORE sconosciuto: {settings.store!r} (firestore | local)")
