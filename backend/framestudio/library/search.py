"""Full-text search over assets with SQLite FTS5 (plan step M1.5).

The index holds, per asset: the prompt of the generation that made it, the
original file name, the caption and the user's notes (element names join in
M4). ``unicode61`` with diacritics removed handles most scripts ("cafe"
finds "café"). Chinese, Japanese and Korean are written without spaces, so
those characters are indexed one per token and searched as phrases.

Search strings may carry tokens: ``type:video``, ``model:mock`` (or
``model:upload``), ``after:2026-11-01``, ``before:2026-11-30``, ``is:liked``,
``is:downloaded``, ``is:uploaded``, ``is:generated``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

FTS_TABLE = "asset_search"
#: bm25 weights per column: asset_id (unindexed), prompt, name, caption, notes.
BM25_WEIGHTS = "0.0, 3.0, 2.0, 1.5, 1.0"

_CJK = re.compile("([⺀-⿟぀-ヿ㄀-ㇿ㐀-䶿一-鿿가-힯豈-﫿ｦ-ﾟ])")
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')
_KINDS = {"image", "video", "audio"}


def segment(value: str) -> str:
    """Put spaces around CJK characters so each one is a token."""
    return _CJK.sub(r" \1 ", value)


@dataclass
class ParsedQuery:
    terms: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    after: datetime | None = None
    before: datetime | None = None
    liked: bool | None = None
    downloaded: bool | None = None
    provenance: str | None = None

    def match(self) -> str | None:
        """FTS5 MATCH expression: every term must appear (prefix match for
        words, adjacent-character phrases for CJK)."""
        parts: list[str] = []
        for term in self.terms:
            if _CJK.search(term):
                chars = segment(term).split()
                parts.append('"' + " ".join(c.replace('"', '""') for c in chars) + '"')
                continue
            words = re.findall(r"\w+", term)
            parts.extend(f'"{w.replace(chr(34), chr(34) * 2)}"*' for w in words)
        return " AND ".join(parts) if parts else None


def _day(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_query(raw: str) -> ParsedQuery:
    parsed = ParsedQuery()
    for quoted, bare in _TOKEN.findall(raw or ""):
        if quoted:
            parsed.terms.append(quoted)
            continue
        key, sep, value = bare.partition(":")
        key, value = key.lower(), value.strip()
        if sep and value:
            if key == "type" and value.lower() in _KINDS:
                parsed.kinds.append(value.lower())
                continue
            if key == "model":
                parsed.models.append(value)
                continue
            if key in {"after", "before"} and (day := _day(value)):
                if key == "after":
                    parsed.after = day
                else:
                    parsed.before = day + timedelta(days=1)
                continue
            if key == "is":
                flag = value.lower()
                if flag in {"liked", "favorite", "favourite"}:
                    parsed.liked = True
                    continue
                if flag == "downloaded":
                    parsed.downloaded = True
                    continue
                if flag in {"uploaded", "generated"}:
                    parsed.provenance = flag
                    continue
        parsed.terms.append(bare)
    return parsed


def index_assets(session: Session, asset_ids: list[str]) -> None:
    """(Re)index assets from their current rows."""
    if not asset_ids:
        return
    remove_from_index(session, asset_ids)
    rows = session.execute(
        text(
            "SELECT a.id, coalesce(g.prompt, ''), coalesce(a.original_name, ''), "
            "coalesce(a.caption, ''), coalesce(a.notes, '') "
            "FROM assets a LEFT JOIN generations g ON g.id = a.source_generation_id "
            "WHERE a.id IN (SELECT value FROM json_each(:ids))"
        ),
        {"ids": _json_list(asset_ids)},
    ).all()
    for asset_id, prompt, name, caption, notes in rows:
        session.execute(
            text(
                f"INSERT INTO {FTS_TABLE} (asset_id, prompt, name, caption, notes) "  # noqa: S608 (constant table name; values are bound)
                "VALUES (:id, :prompt, :name, :caption, :notes)"
            ),
            {
                "id": asset_id,
                "prompt": segment(prompt),
                # File names: split on separators so "tuna_kitchen-02.png" finds "kitchen".
                "name": segment(re.sub(r"[_\-.]+", " ", name)),
                "caption": segment(caption),
                "notes": segment(notes),
            },
        )


def remove_from_index(session: Session, asset_ids: list[str]) -> None:
    if asset_ids:
        session.execute(
            text(f"DELETE FROM {FTS_TABLE} WHERE asset_id IN (SELECT value FROM json_each(:ids))"),  # noqa: S608 (constant table name; values are bound)
            {"ids": _json_list(asset_ids)},
        )


def _json_list(values: list[str]) -> str:
    return json.dumps(values)
