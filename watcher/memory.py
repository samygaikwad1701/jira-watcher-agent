"""Persistent memory of tickets we have seen and the categories the LLM assigned.

Stored as one JSON file at `~/.watcher/memory.json` (override with
`WATCHER_MEMORY_PATH`). Keyed by ticket key so re-runs never re-ask the LLM
to categorize the same unchanged ticket — this is the single biggest token
saver in watch mode.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = (
    "bug",
    "feature",
    "enhancement",
    "refactor",
    "infra",
    "docs",
    "testing",
    "performance",
    "security",
    "dependency",
    "data",
    "ux",
    "support",
    "other",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TicketRecord:
    key: str
    category: str
    signature: str  # (key, updated) — re-categorize only when this changes
    summary: str
    first_seen: str
    last_seen: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "category": self.category,
            "signature": self.signature,
            "summary": self.summary,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> TicketRecord:
        return cls(
            key=raw["key"],
            category=raw.get("category", "other"),
            signature=raw.get("signature", ""),
            summary=raw.get("summary", ""),
            first_seen=raw.get("first_seen", _now_iso()),
            last_seen=raw.get("last_seen", _now_iso()),
        )


@dataclass
class Memory:
    path: Path
    tickets: dict[str, TicketRecord] = field(default_factory=dict)

    def get(self, key: str) -> TicketRecord | None:
        return self.tickets.get(key)

    def upsert(self, key: str, signature: str, summary: str, category: str) -> TicketRecord:
        now = _now_iso()
        existing = self.tickets.get(key)
        if existing:
            existing.signature = signature
            existing.summary = summary
            existing.category = category
            existing.last_seen = now
            return existing
        record = TicketRecord(
            key=key,
            category=category,
            signature=signature,
            summary=summary,
            first_seen=now,
            last_seen=now,
        )
        self.tickets[key] = record
        return record

    def category_counts(self, keys: Iterable[str] | None = None) -> dict[str, int]:
        pool = (
            [self.tickets[k] for k in keys if k in self.tickets]
            if keys is not None
            else list(self.tickets.values())
        )
        return dict(Counter(r.category for r in pool))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "categories": list(CATEGORIES),
            "tickets": {k: r.to_dict() for k, r in self.tickets.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2))


def load_memory(path_str: str) -> Memory:
    path = Path(path_str).expanduser()
    if not path.exists():
        return Memory(path=path)
    try:
        raw = json.loads(path.read_text() or "{}")
    except json.JSONDecodeError:
        return Memory(path=path)
    tickets_raw = raw.get("tickets") or {}
    tickets = {k: TicketRecord.from_dict(v) for k, v in tickets_raw.items()}
    return Memory(path=path, tickets=tickets)
