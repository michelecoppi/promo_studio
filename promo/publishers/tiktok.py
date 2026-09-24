"""TikTok come **bozza** (§5.5): Content Posting API, caricamento nella casella dell'utente.

Il video arriva nelle notifiche/bozze dell'account TikTok; la persona lo apre nell'app,
aggiunge l'audio di tendenza e la didascalia (copiata dall'admin) e pubblica. Lo strumento non
pubblica mai direttamente su TikTok.

Flusso (API v2, scope `video.upload`):
1. `POST /v2/oauth/token/` con `grant_type=refresh_token` → access token (dura ~24 h);
   se TikTok restituisce un refresh token nuovo lo si salva (`TokenStore`).
2. `POST /v2/post/publish/inbox/video/init/` con `source_info.source = FILE_UPLOAD` →
   `publish_id` e `upload_url`.
3. `PUT upload_url` del file, a pezzi con `Content-Range`.
4. `POST /v2/post/publish/status/fetch/` finche' lo stato e' `SEND_TO_USER_INBOX`
   (arrivato) o `FAILED`.

Da verificare su developers.tiktok.com prima di attivarlo (docs/promo-studio.md §TikTok):
scope approvati per l'app, limiti di frequenza, regole dei pezzi, eventuali restrizioni per
le app non ancora revisionate.
"""
import json
import math
import os
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from promo import log
from promo.publishers.base import Publisher, PublishResult

API = "https://open.tiktokapis.com"
TOKEN_URL = f"{API}/v2/oauth/token/"
INIT_URL = f"{API}/v2/post/publish/inbox/video/init/"
STATUS_URL = f"{API}/v2/post/publish/status/fetch/"
TIMEOUT = 60

MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
STATUS_POLLS = 10
POLL_SECONDS = 3

DONE_STATES = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}


class TikTokError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------
# Refresh token
# ---------------------------------------------------------------------------------------

class TokenStore:
    """Dove sta il refresh token. Di default in memoria (dalla variabile d'ambiente)."""

    def __init__(self, token: str = ""):
        self._token = token

    def load(self) -> str:
        return self._token

    def save(self, token: str) -> bool:
        """True se il token nuovo e' stato salvato in modo durevole."""
        self._token = token
        return False


class FileTokenStore(TokenStore):
    """File fuori dal repository con permessi 600 (per l'uso sulla macchina del maintainer)."""

    def __init__(self, path, fallback: str = ""):
        self.path = Path(path).expanduser()
        super().__init__(fallback)

    def load(self) -> str:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8")).get("refresh_token", "") or self._token
        return self._token

    def save(self, token: str) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": token}, fh)
        self._token = token
        return True


class SecretManagerTokenStore(TokenStore):
    """Google Secret Manager (`projects/<p>/secrets/<nome>`): legge l'ultima versione e ne
    aggiunge una nuova quando TikTok ruota il token. Usa le credenziali di default
    dell'ambiente (Workload Identity nel workflow), via google-auth di firebase-admin."""

    def __init__(self, secret: str, fallback: str = "", session=None):
        super().__init__(fallback)
        self.secret = secret.rstrip("/")
        self.session = session

    def _authed(self):
        if self.session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self.session = AuthorizedSession(credentials)
        return self.session

    def load(self) -> str:
        import base64
        url = f"https://secretmanager.googleapis.com/v1/{self.secret}/versions/latest:access"
        response = self._authed().get(url, timeout=TIMEOUT)
        if response.status_code != 200:
            log.warning("Secret Manager: lettura del refresh token fallita (HTTP %s)", response.status_code)
            return self._token
        return base64.b64decode(response.json()["payload"]["data"]).decode("utf-8").strip()

    def save(self, token: str) -> bool:
        import base64
        url = f"https://secretmanager.googleapis.com/v1/{self.secret}:addVersion"
        payload = {"payload": {"data": base64.b64encode(token.encode("utf-8")).decode("ascii")}}
        response = self._authed().post(url, json=payload, timeout=TIMEOUT)
        self._token = token
        return response.status_code == 200


# ---------------------------------------------------------------------------------------

def chunk_plan(size: int) -> tuple:
    """(chunk_size, total_chunk_count) secondo le regole dell'API.

    Sotto i 64 MB un pezzo solo (sotto i 5 MB e' obbligatorio). Sopra, pezzi da 10 MB e
    l'ultimo assorbe il resto: `total_chunk_count = floor(size / chunk_size)`."""
    if size <= 0:
        raise ValueError("file vuoto")
    if size <= MAX_CHUNK:
        return size, 1
    return DEFAULT_CHUNK, size // DEFAULT_CHUNK


class TikTokDraftPublisher(Publisher):
    channel = "tiktok"

    def __init__(self, client_key: str, client_secret: str, tokens: TokenStore, session=None,
                 sleep: Callable[[float], None] = time.sleep):
        self.client_key = client_key
        self.client_secret = client_secret
        self.tokens = tokens
        self.session = session or requests.Session()
        self.sleep = sleep
        self._access_token: Optional[str] = None

    # --- token ------------------------------------------------------------------------
    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        refresh = self.tokens.load()
        if not (self.client_key and self.client_secret and refresh):
            raise TikTokError("TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET o il refresh token non configurati")
        response = self.session.post(TOKEN_URL, data={
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT)
        data = _json(response)
        token = data.get("access_token")
        if not token:
            raise TikTokError(f"rinnovo del token fallito: {data.get('error_description') or data.get('error') or response.status_code}")
        log.register_secrets([token])
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != refresh:
            log.register_secrets([new_refresh])
            if not self.tokens.save(new_refresh):
                log.warning("TikTok ha ruotato il refresh token ma non c'e' un posto dove salvarlo: "
                            "configura TIKTOK_REFRESH_TOKEN_SECRET (Secret Manager) o PROMO_TIKTOK_TOKEN_FILE")
        self._access_token = token
        return token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json; charset=UTF-8"}

    # --- pubblicazione ----------------------------------------------------------------
    def describe(self, post, media_path):
        size = os.path.getsize(media_path) if os.path.exists(media_path) else 0
        chunk, count = chunk_plan(size) if size else (0, 0)
        return f"upload come bozza TikTok di {os.path.basename(media_path)} ({size} byte, {count} pezzi)"

    def publish(self, post, media_path) -> PublishResult:
        try:
            size = os.path.getsize(media_path)
            chunk, count = chunk_plan(size)
            init = self.session.post(INIT_URL, headers=self._headers(), json={
                "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                                "total_chunk_count": count},
            }, timeout=TIMEOUT)
            data = _api_data(init)
            publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
            if not publish_id or not upload_url:
                raise TikTokError("risposta di init senza publish_id/upload_url")
            self._upload(upload_url, media_path, size, chunk, count)
            state = self._wait(publish_id)
        except (TikTokError, requests.RequestException, OSError, ValueError) as e:
            return PublishResult.failure(log.scrub(e))
        if state not in DONE_STATES:
            return PublishResult(ok=False, external_id=None,
                                 error=f"TikTok: stato {state} per publish_id {publish_id}")
        # Nessun URL pubblico: il video e' nella casella dell'account, non ancora pubblicato.
        return PublishResult(ok=True, external_id=publish_id, external_url=None)

    def _upload(self, url, path, size, chunk, count):
        with open(path, "rb") as fh:
            for index in range(count):
                start = index * chunk
                end = size - 1 if index == count - 1 else start + chunk - 1
                fh.seek(start)
                body = fh.read(end - start + 1)
                response = self.session.put(url, data=body, headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                }, timeout=TIMEOUT * 5)
                if response.status_code not in (200, 201, 206):
                    raise TikTokError(f"upload del pezzo {index + 1}/{count} fallito: HTTP {response.status_code}")

    def _wait(self, publish_id) -> str:
        state = "UNKNOWN"
        for attempt in range(STATUS_POLLS):
            response = self.session.post(STATUS_URL, headers=self._headers(), json={"publish_id": publish_id},
                                         timeout=TIMEOUT)
            data = _api_data(response)
            state = data.get("status") or state
            if state in DONE_STATES:
                return state
            if state == "FAILED":
                raise TikTokError(f"TikTok ha rifiutato il video: {data.get('fail_reason') or 'motivo non indicato'}")
            self.sleep(POLL_SECONDS * math.pow(1.5, attempt))
        return state


def _json(response) -> dict:
    try:
        return response.json()
    except ValueError:
        raise TikTokError(f"risposta non JSON (HTTP {response.status_code})")


def _api_data(response) -> dict:
    body = _json(response)
    error = body.get("error") or {}
    if error.get("code") not in (None, "ok"):
        raise TikTokError(f"{error.get('code')}: {error.get('message') or ''}".strip())
    if response.status_code != 200:
        raise TikTokError(f"HTTP {response.status_code}")
    return body.get("data") or {}


def token_store(settings) -> TokenStore:
    if settings.tiktok_refresh_token_secret:
        return SecretManagerTokenStore(settings.tiktok_refresh_token_secret, settings.tiktok_refresh_token)
    token_file = os.environ.get("PROMO_TIKTOK_TOKEN_FILE")
    if token_file:
        return FileTokenStore(token_file, settings.tiktok_refresh_token)
    return TokenStore(settings.tiktok_refresh_token)
