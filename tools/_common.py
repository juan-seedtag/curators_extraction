"""Shared utilities: Trino access (re-exported) and CSV export."""

from __future__ import annotations

import csv
from pathlib import Path

from tools.trino_client import run_trino_query  # noqa: F401  (public re-export)

__all__ = ["run_trino_query", "save_csv"]


def save_csv(rows: list[dict], path: Path) -> Path:
    """Save a list of dicts to CSV, unioning keys across all rows."""
    path = Path(path)
    if rows:
        all_keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return path
