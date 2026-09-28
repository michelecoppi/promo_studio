"""Collegamento dell'account TikTok (OAuth v2 di Login Kit), una volta sola.

1. `authorize_url()` → la persona apre il link, fa il login con l'account TikTok del gioco e
   autorizza l'app;
2. TikTok rimanda al Redirect URI registrato (di default la pagina /privacy del gioco) con
   `?code=...&state=...` nell'indirizzo: la persona copia quell'indirizzo;
3. `connect()` controlla lo state, scambia il code con i token e legge il nome dell'account.

Il refresh token che ne esce va in TIKTOK_REFRESH_TOKEN (vedi publishers/tiktok.py).
"""
import secrets
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from promo import log
from promo.publishers.tiktok import TIMEOUT, TOKEN_URL, TikTokError, _json

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
SCOPES = ("user.info.basic", "video.upload")


def new_state() -> str:
    return secrets.token_urlsafe(16)


def authorize_url(client_key: str, redirect_uri: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_key": client_key, "scope": ",".join(SCOPES), "response_type": "code",
        "redirect_uri": redirect_uri, "state": state,
    })


def parse_callback(url: str, expected_state: Optional[str]) -> str:
    """Il `code` dall'indirizzo di ritorno (incollato per intero, o solo il code)."""
    url = url.strip()
    if "code=" not in url and "error=" not in url and "://" not in url and url:
        return url  # solo il code
    query = parse_qs(urlparse(url).query)
    if "error" in query:
        raise TikTokError(f"autorizzazione negata: {query.get('error_description', query['error'])[0]}")
    code = (query.get("code") or [""])[0]
    if not code:
        raise TikTokError("nell'indirizzo non c'e' il parametro code: copia l'URL completo dopo il login")
    if expected_state and (query.get("state") or [""])[0] != expected_state:
        raise TikTokError("state diverso da quello atteso: riparti dal link di autorizzazione")
    return code


def connect(client_key: str, client_secret: str, redirect_uri: str, code: str, session=None) -> dict:
    """{'refresh_token', 'display_name', 'scope'} dopo lo scambio del code."""
    session = session or requests.Session()
    response = session.post(TOKEN_URL, data={
        "client_key": client_key, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT)
    data = _json(response)
    access, refresh = data.get("access_token"), data.get("refresh_token")
    if not (access and refresh):
        raise TikTokError(f"scambio del code fallito: {data.get('error_description') or data.get('error') or response.status_code}")
    log.register_secrets([access, refresh])
    scope = data.get("scope") or ""
    if "video.upload" not in scope:
        raise TikTokError(f"l'autorizzazione non include video.upload (scope: {scope or 'nessuno'})")
    display_name = ""
    try:
        info = session.get(USER_INFO_URL, params={"fields": "open_id,display_name"},
                           headers={"Authorization": f"Bearer {access}"}, timeout=TIMEOUT)
        display_name = ((_json(info).get("data") or {}).get("user") or {}).get("display_name") or ""
    except (TikTokError, requests.RequestException):
        pass  # il nome e' solo per mostrarlo: il collegamento vale lo stesso
    return {"refresh_token": refresh, "display_name": display_name, "scope": scope}
