"""
Export der Historie als CSV und JSON (fuer die Oberflaeche).
Beide Formate enthalten Text- UND numerische Felder.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

from .store import COLUMNS, HistoryStore


def to_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c) for c in COLUMNS})
    return buf.getvalue()


def to_json(rows: List[Dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2)


def export(store: HistoryStore, fmt: str = "csv", **query) -> str:
    rows = store.query(**query)
    if fmt.lower() == "json":
        return to_json(rows)
    return to_csv(rows)
