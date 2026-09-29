"""Build a compact prompt for the chosen LLM CLI and parse its digest.

Token optimization strategy:
- Short JSON field names (`k`, `s`, `p`, `u`, `c`, `t`, `d`).
- Days-since-updated integer instead of full timestamps.
- Descriptions and comments included ONLY for tickets that still need
  categorization; known tickets ship without them.
- One recent comment max, capped at 200 chars.
- Summaries capped at 200 chars, descriptions at 300, comments at 200.
- Categories are cached in memory — re-runs never re-categorize unchanged tickets.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Iterable

from .jira_client import Ticket
from .memory import CATEGORIES, Memory
from .providers import run_provider


CATEGORY_BLOCK_MARKER = "===CATEGORIES==="

SYSTEM_INSTRUCTIONS = f"""You triage JIRA tickets for an engineer.

You receive a JSON payload:
- `known`: tickets already tagged with a category (use as-is).
- `to_tag`: tickets that need a category picked from this list:
  {', '.join(CATEGORIES)}.

Produce OUTPUT in two parts separated by the literal line `{CATEGORY_BLOCK_MARKER}`.

Part 1 — markdown digest, terse, no preamble:
1. One-line overview (total, breakdown by category).
2. Urgent bullets: high priority, overdue (due_date past), or stalled (u > 7).
3. Grouped bullets by category. Each bullet: `KEY — summary (priority, ud)`
   where `u` is days since updated. Include no URLs; the caller will add them.
4. One-sentence recommendation on what to focus on.

Part 2 — after `{CATEGORY_BLOCK_MARKER}`, one line per ticket in `to_tag`:
`KEY: category` (category MUST be from the list; use `other` if unsure).
No JSON, no commentary, no extra whitespace.
"""


def _days_since(iso_ts: str) -> int:
    if not iso_ts:
        return -1
    try:
        # JIRA returns e.g. "2026-09-20T14:23:00.000+0000"
        dt = datetime.strptime(iso_ts[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return -1
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _trim(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\r", "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _compact(t: Ticket, *, include_body: bool) -> dict:
    payload = {
        "k": t.key,
        "t": _trim(t.summary, 200),
        "s": t.status,
        "p": t.priority,
        "u": _days_since(t.updated),
    }
    if t.due_date:
        payload["due"] = t.due_date
    if include_body:
        desc = _trim(t.description, 300)
        if desc:
            payload["d"] = desc
        if t.comments:
            payload["lc"] = _trim(t.comments[-1].body, 200)
    return payload


def _parse_response(text: str) -> tuple[str, dict[str, str]]:
    if CATEGORY_BLOCK_MARKER not in text:
        return text.strip(), {}
    digest, _, tail = text.partition(CATEGORY_BLOCK_MARKER)
    assignments: dict[str, str] = {}
    for line in tail.splitlines():
        m = re.match(r"\s*([A-Z][A-Z0-9]+-\d+)\s*[:\-]\s*([a-z\-]+)\s*$", line)
        if not m:
            continue
        key, cat = m.group(1), m.group(2)
        assignments[key] = cat if cat in CATEGORIES else "other"
    return digest.strip(), assignments


def _add_urls(digest: str, tickets: list[Ticket]) -> str:
    """Wrap bare KEY tokens in the digest as markdown links."""
    url_by_key = {t.key: t.url for t in tickets}
    if not url_by_key:
        return digest

    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in url_by_key) + r")\b")

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        # avoid double-linking if already inside a markdown link
        return f"[{key}]({url_by_key[key]})"

    return pattern.sub(_sub, digest)


def build_prompt(tickets: list[Ticket], memory: Memory) -> tuple[str, list[Ticket]]:
    """Return (prompt_text, list_of_tickets_needing_categorization)."""
    known: list[dict] = []
    to_tag: list[Ticket] = []

    for t in tickets:
        record = memory.get(t.key)
        sig = f"{t.key}|{t.updated}"
        if record and record.signature == sig and record.category in CATEGORIES:
            entry = _compact(t, include_body=False)
            entry["c"] = record.category
            known.append(entry)
        else:
            to_tag.append(t)

    payload = {
        "known": known,
        "to_tag": [_compact(t, include_body=True) for t in to_tag],
    }

    prompt = (
        SYSTEM_INSTRUCTIONS
        + "\n\nINPUT:\n"
        + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )
    return prompt, to_tag


def summarize(
    tickets: list[Ticket],
    memory: Memory,
    provider: str,
    cli_override: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Run the LLM, update memory with new category assignments, return
    (digest_markdown, new_category_assignments)."""
    if not tickets:
        return "No tickets currently assigned to you.", {}

    prompt, to_tag = build_prompt(tickets, memory)
    raw = run_provider(provider, prompt, cli_override=cli_override)
    digest, assignments = _parse_response(raw)

    # Update memory: freshly-tagged tickets + refresh last_seen for known ones.
    tag_by_key = {t.key: t for t in to_tag}
    for key, category in assignments.items():
        t = tag_by_key.get(key)
        if not t:
            continue
        memory.upsert(
            key=key,
            signature=f"{t.key}|{t.updated}",
            summary=_trim(t.summary, 200),
            category=category,
        )
    for t in tickets:
        if memory.get(t.key) and t.key not in assignments:
            memory.upsert(
                key=t.key,
                signature=f"{t.key}|{t.updated}",
                summary=_trim(t.summary, 200),
                category=memory.get(t.key).category,
            )

    memory.save()
    return _add_urls(digest, tickets), assignments


def rule_based_digest(tickets: list[Ticket], memory: Memory) -> str:
    """Zero-LLM fallback used with --no-llm; still uses cached categories."""
    if not tickets:
        return "No tickets currently assigned to you."

    counts = memory.category_counts(t.key for t in tickets)
    header = f"**{len(tickets)} tickets assigned**"
    if counts:
        header += " · " + ", ".join(
            f"{c} {n}" for c, n in sorted(counts.items(), key=lambda x: -x[1])
        )

    by_status: dict[str, list[Ticket]] = {}
    for t in tickets:
        by_status.setdefault(t.status or "Unknown", []).append(t)

    lines = [header, ""]
    for status, group in sorted(by_status.items()):
        lines.append(f"### {status} ({len(group)})")
        for t in group:
            rec = memory.get(t.key)
            cat = f" · _{rec.category}_" if rec else ""
            lines.append(
                f"- [{t.key}]({t.url}) — {_trim(t.summary, 120)} "
                f"_({t.priority}, {_days_since(t.updated)}d ago){cat}_"
            )
        lines.append("")
    return "\n".join(lines).strip()
