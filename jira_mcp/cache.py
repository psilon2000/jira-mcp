from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings


@dataclass(frozen=True)
class CacheHit:
    key: str
    payload: dict[str, Any]
    age_seconds: int
    updated: str | None = None


class JiraCache:
    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.enable_cache
        self._path = Path(settings.cache_path).expanduser()
        self._ttl_seconds = max(1, settings.cache_ttl_seconds)
        self._max_entries = max(1, settings.cache_max_entries)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_issue(
        self,
        issue_key: str,
        fields: list[str] | None,
        expand: list[str] | None,
        allow_stale: bool = False,
    ) -> CacheHit | None:
        if not self._enabled:
            return None

        key = self.issue_key(issue_key, fields, expand)
        entry = self._load().get("issues", {}).get(key)
        hit = self._hit_from_entry(key, entry)
        if hit is None:
            return None
        if not allow_stale and hit.age_seconds > self._ttl_seconds:
            return None
        return hit

    def put_issue(
        self,
        issue_key: str,
        fields: list[str] | None,
        expand: list[str] | None,
        issue: dict[str, Any],
    ) -> None:
        if not self._enabled:
            return

        data = self._load()
        issues = self._entries(data, "issues")
        issues[self.issue_key(issue_key, fields, expand)] = self._entry(
            payload=issue,
            issue_key=issue_key.strip().upper(),
            fields=self._normalize_list(fields),
            expand=self._normalize_list(expand),
            updated=self._extract_updated(issue),
        )
        self._prune(data)
        self._save(data)

    def touch_issue(self, key: str) -> None:
        if not self._enabled:
            return

        data = self._load()
        entry = data.get("issues", {}).get(key)
        if isinstance(entry, dict):
            entry["saved_at"] = time.time()
            self._save(data)

    def invalidate_issue(self, issue_key: str) -> None:
        if not self._enabled:
            return

        issue = issue_key.strip().upper()
        if not issue:
            return

        data = self._load()
        issues = self._entries(data, "issues")
        for key, entry in list(issues.items()):
            if isinstance(entry, dict) and entry.get("issue_key") == issue:
                del issues[key]
        self._entries(data, "searches").clear()
        self._save(data)

    def clear_searches(self) -> None:
        if not self._enabled:
            return

        data = self._load()
        self._entries(data, "searches").clear()
        self._save(data)

    def get_search(self, jql: str, fields: list[str] | None, limit: int) -> CacheHit | None:
        if not self._enabled:
            return None

        key = self.search_key(jql, fields, limit)
        entry = self._load().get("searches", {}).get(key)
        hit = self._hit_from_entry(key, entry)
        if hit is None or hit.age_seconds > self._ttl_seconds:
            return None
        return hit

    def put_search(self, jql: str, fields: list[str] | None, limit: int, result: dict[str, Any]) -> None:
        if not self._enabled:
            return

        data = self._load()
        searches = self._entries(data, "searches")
        searches[self.search_key(jql, fields, limit)] = self._entry(
            payload=result,
            jql=jql.strip(),
            fields=self._normalize_list(fields),
            limit=limit,
        )

        issues = self._entries(data, "issues")
        for issue in result.get("issues", []) or []:
            if not isinstance(issue, dict):
                continue
            issue_key = str(issue.get("key") or "").strip().upper()
            if not issue_key:
                continue
            issues[self.issue_key(issue_key, fields, None)] = self._entry(
                payload=issue,
                issue_key=issue_key,
                fields=self._normalize_list(fields),
                expand=None,
                updated=self._extract_updated(issue),
            )

        self._prune(data)
        self._save(data)

    def search_text(self, query: str, limit: int) -> dict[str, Any]:
        if not self._enabled:
            return {
                "status": "ok",
                "query": query,
                "count": 0,
                "issues": [],
                "cache": {"enabled": False},
            }

        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms:
            raise ValueError("query is required")

        now = time.time()
        limit = max(1, min(limit, 200))
        by_issue: dict[str, tuple[float, dict[str, Any], int]] = {}
        searched_entries = 0
        for entry in self._load().get("issues", {}).values():
            if not isinstance(entry, dict):
                continue
            saved_at = self._saved_at(entry)
            age_seconds = int(max(0, now - saved_at))
            if age_seconds > self._ttl_seconds:
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            searched_entries += 1
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
            if not all(term in text for term in terms):
                continue
            issue_key = str(payload.get("key") or entry.get("issue_key") or "").strip().upper()
            if not issue_key:
                issue_key = str(len(by_issue))
            current = by_issue.get(issue_key)
            if current is None or saved_at > current[0]:
                by_issue[issue_key] = (saved_at, payload, age_seconds)

        matches = sorted(by_issue.values(), key=lambda item: item[0], reverse=True)[:limit]
        return {
            "status": "ok",
            "query": query,
            "count": len(matches),
            "issues": [payload for _, payload, _ in matches],
            "cache": {
                "enabled": True,
                "hit": True,
                "searched_entries": searched_entries,
                "ttl_seconds": self._ttl_seconds,
                "max_age_seconds": max((age for _, _, age in matches), default=0),
            },
        }

    def issue_key(self, issue_key: str, fields: list[str] | None, expand: list[str] | None) -> str:
        return self._stable_key(
            "issue",
            {
                "issue_key": issue_key.strip().upper(),
                "fields": self._normalize_list(fields),
                "expand": self._normalize_list(expand),
            },
        )

    def search_key(self, jql: str, fields: list[str] | None, limit: int) -> str:
        return self._stable_key(
            "search",
            {
                "jql": jql.strip(),
                "fields": self._normalize_list(fields),
                "limit": max(1, min(limit, 200)),
            },
        )

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return self._empty()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty()
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return self._empty()
        self._entries(payload, "issues")
        self._entries(payload, "searches")
        return payload

    def _save(self, data: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(tmp_path, self._path)
        except OSError:
            return

    def _prune(self, data: dict[str, Any]) -> None:
        now = time.time()
        for section in ("issues", "searches"):
            entries = self._entries(data, section)
            for key, entry in list(entries.items()):
                if not isinstance(entry, dict) or now - self._saved_at(entry) > self._ttl_seconds:
                    del entries[key]

            overflow = len(entries) - self._max_entries
            if overflow <= 0:
                continue
            sorted_keys = sorted(entries, key=lambda key: self._saved_at(entries[key]))
            for key in sorted_keys[:overflow]:
                del entries[key]

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": 1, "issues": {}, "searches": {}}

    @staticmethod
    def _entries(data: dict[str, Any], section: str) -> dict[str, Any]:
        entries = data.get(section)
        if not isinstance(entries, dict):
            entries = {}
            data[section] = entries
        return entries

    @staticmethod
    def _entry(payload: dict[str, Any], **metadata: Any) -> dict[str, Any]:
        entry = dict(metadata)
        entry["saved_at"] = time.time()
        entry["payload"] = payload
        return entry

    @staticmethod
    def _hit_from_entry(key: str, entry: Any) -> CacheHit | None:
        if not isinstance(entry, dict):
            return None
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            return None
        saved_at = JiraCache._saved_at(entry)
        return CacheHit(
            key=key,
            payload=payload,
            age_seconds=int(max(0, time.time() - saved_at)),
            updated=entry.get("updated") if isinstance(entry.get("updated"), str) else None,
        )

    @staticmethod
    def _saved_at(entry: dict[str, Any]) -> float:
        value = entry.get("saved_at")
        return value if isinstance(value, (int, float)) else 0.0

    @staticmethod
    def _extract_updated(issue: dict[str, Any]) -> str | None:
        fields = issue.get("fields")
        if not isinstance(fields, dict):
            return None
        updated = fields.get("updated")
        return updated if isinstance(updated, str) and updated else None

    @staticmethod
    def _normalize_list(values: list[str] | None) -> list[str] | None:
        if not values:
            return None
        normalized = sorted({str(value).strip() for value in values if str(value).strip()})
        return normalized or None

    @staticmethod
    def _stable_key(kind: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"{kind}:{digest}"
