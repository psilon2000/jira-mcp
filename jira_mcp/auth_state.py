from __future__ import annotations

import json
import threading
from pathlib import Path

from .config import Settings


COOKIE_SOURCE_NONE = "none"
COOKIE_SOURCE_CONFIGURED = "configured_env"
COOKIE_SOURCE_INTERNAL = "internal_recovered"


class JiraRuntimeAuthState:
    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self._configured_cookie = self._normalize_cookie(settings.cookie)
        self._storage_path = Path(settings.internal_cookie_storage_path).expanduser().resolve()
        self._internal_cookie = self._load_internal_cookie()
        self._active_cookie: str | None = None
        self._active_source = COOKIE_SOURCE_NONE
        self._bootstrap_active_cookie()

    def get_cookie(self) -> str | None:
        with self._lock:
            return self._active_cookie

    def get_active_source(self) -> str:
        with self._lock:
            return self._active_source

    def promote_internal_cookie(self, cookie: str | None) -> None:
        normalized = self._normalize_cookie(cookie)
        with self._lock:
            self._internal_cookie = normalized
            self._active_cookie = normalized
            self._active_source = COOKIE_SOURCE_INTERNAL if normalized else COOKIE_SOURCE_NONE
            self._persist_internal_cookie(normalized)

    def mark_active_cookie_invalid(self) -> bool:
        with self._lock:
            if self._active_source == COOKIE_SOURCE_INTERNAL:
                self._active_cookie = self._configured_cookie
                self._active_source = (
                    COOKIE_SOURCE_CONFIGURED if self._configured_cookie else COOKIE_SOURCE_NONE
                )
                return self._active_source == COOKIE_SOURCE_CONFIGURED

            if self._active_source == COOKIE_SOURCE_CONFIGURED:
                self._active_cookie = None
                self._active_source = COOKIE_SOURCE_NONE

            return False

    def reset_to_preferred_source(self) -> None:
        with self._lock:
            self._internal_cookie = self._load_internal_cookie()
            self._bootstrap_active_cookie()

    def _bootstrap_active_cookie(self) -> None:
        if self._internal_cookie:
            self._active_cookie = self._internal_cookie
            self._active_source = COOKIE_SOURCE_INTERNAL
            return
        self._active_cookie = self._configured_cookie
        self._active_source = COOKIE_SOURCE_CONFIGURED if self._configured_cookie else COOKIE_SOURCE_NONE

    def _load_internal_cookie(self) -> str | None:
        if not self._storage_path.exists():
            return None
        try:
            payload = json.loads(self._storage_path.read_text())
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return self._normalize_cookie(payload.get("cookie"))

    def _persist_internal_cookie(self, cookie: str | None) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not cookie:
            if self._storage_path.exists():
                self._storage_path.unlink()
            return
        temp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        temp_path.write_text(json.dumps({"cookie": cookie}))
        temp_path.replace(self._storage_path)

    @staticmethod
    def _normalize_cookie(cookie: str | None) -> str | None:
        if cookie is None:
            return None
        cookie = cookie.strip()
        return cookie or None
