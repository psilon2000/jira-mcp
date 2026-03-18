from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .auth_state import JiraRuntimeAuthState
from .config import Settings


@dataclass(frozen=True)
class RecoveryResult:
    is_success: bool
    details: str
    cookie: str | None = None

    @classmethod
    def success(cls, details: str, cookie: str) -> "RecoveryResult":
        return cls(is_success=True, details=details, cookie=cookie)

    @classmethod
    def failure(cls, details: str) -> "RecoveryResult":
        return cls(is_success=False, details=details, cookie=None)


class BrowserRecoveryService:
    def __init__(self, settings: Settings, auth_state: JiraRuntimeAuthState) -> None:
        self._settings = settings
        self._auth_state = auth_state
        self._last_attempt_at = 0.0

    def try_recover(self, reason: str) -> RecoveryResult:
        if not self._settings.enable_browser_recovery:
            return RecoveryResult.failure("Browser recovery is disabled in Jira config")

        if not self._can_attempt_now():
            return RecoveryResult.failure("browser_recovery_skipped_cooldown")

        script_path = Path(self._settings.browser_recovery_script_path).expanduser().resolve()
        if not script_path.exists():
            return RecoveryResult.failure(f"Browser recovery script not found: {script_path}")

        self._last_attempt_at = time.monotonic()
        command = [
            sys.executable,
            str(script_path),
            "--base-url",
            self._settings.base_url,
            "--reason",
            reason,
        ]
        if self._settings.username:
            command.extend(["--username", self._settings.username])
        if self._settings.password:
            command.extend(["--password", self._settings.password])
        if self._settings.browser_profile_dir:
            command.extend(["--profile-dir", self._settings.browser_profile_dir])

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(self._settings.timeout_sec, 30),
        )
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        if result.returncode != 0:
            return RecoveryResult.failure(output or f"Browser recovery helper exited with code {result.returncode}")

        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return RecoveryResult.failure("Browser recovery helper returned invalid JSON")

        if not isinstance(payload, dict):
            return RecoveryResult.failure("Browser recovery helper returned invalid payload")
        if not payload.get("success"):
            return RecoveryResult.failure(str(payload.get("details") or "Browser recovery did not succeed"))

        cookie = payload.get("cookie")
        if not isinstance(cookie, str) or not cookie.strip():
            return RecoveryResult.failure(str(payload.get("details") or "Browser recovery did not return a valid cookie"))

        cookie = cookie.strip()
        self._auth_state.promote_internal_cookie(cookie)
        return RecoveryResult.success(str(payload.get("details") or "Browser recovery succeeded"), cookie)

    def _can_attempt_now(self) -> bool:
        cooldown_sec = max(1, self._settings.browser_recovery_cooldown_minutes) * 60
        return (time.monotonic() - self._last_attempt_at) >= cooldown_sec
