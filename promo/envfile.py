"""Lettura e scrittura del file .env locale (per la scheda Impostazioni della dashboard).

Aggiorna solo le chiavi toccate: commenti, ordine e righe sconosciute restano come sono.
Un valore None toglie la chiave. Dopo `update` anche os.environ e' allineato, cosi' il
prossimo `config.load()` vede subito i valori nuovi (load_dotenv non sovrascrive).
"""
import os
import re
from pathlib import Path
from typing import Mapping, Optional

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    if value == "" or re.search(r"[\s#\"']", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def read(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line)
        if match:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def update(path: Path, changes: Mapping[str, Optional[str]], environ=None) -> None:
    path = Path(path)
    environ = os.environ if environ is None else environ
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(changes)
    out = []
    for line in lines:
        match = _LINE.match(line)
        key = match.group(1) if match else None
        if key in pending:
            value = pending.pop(key)
            if value is not None:
                out.append(f"{key}={_quote(value)}")
            continue
        out.append(line)
    out.extend(f"{key}={_quote(value)}" for key, value in pending.items() if value is not None)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    for key, value in changes.items():
        if value is None:
            environ.pop(key, None)
        else:
            environ[key] = value
