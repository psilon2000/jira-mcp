from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


AUTH_MODE_COOKIE = "cookie"
AUTH_MODE_BASIC = "basic"
AUTH_MODE_BASIC_WITH_COOKIES = "basic_with_cookies"
AUTH_MODE_BEARER = "bearer"
AUTH_MODE_AUTO = "auto"

ALLOWED_AUTH_MODES = {
    AUTH_MODE_COOKIE,
    AUTH_MODE_BASIC,
    AUTH_MODE_BASIC_WITH_COOKIES,
    AUTH_MODE_BEARER,
    AUTH_MODE_AUTO,
}


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_version: int
    auth_mode: str
    cookie: str | None
    username: str | None
    password: str | None
    token: str | None
    timeout_sec: int
    default_limit: int
    write_project_whitelist: tuple[str, ...]
    write_issue_whitelist: tuple[str, ...]
    enable_create_issue: bool
    create_issue_project_whitelist: tuple[str, ...]
    enable_browser_recovery: bool
    browser_recovery_script_path: str
    browser_profile_dir: str
    internal_cookie_storage_path: str
    browser_recovery_cooldown_minutes: int

    @property
    def rest_root(self) -> str:
        return f"{self.base_url.rstrip('/')}/rest/api/{self.api_version}"


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _project_key(name: str) -> str | None:
    value = _clean(os.getenv(name))
    return value.upper() if value else None


def _create_issue_project_whitelist() -> tuple[str, ...]:
    projects = _csv("JIRA_CREATE_ISSUE_PROJECT_WHITELIST")
    if projects:
        return projects

    legacy_project = _project_key("JIRA_CREATE_ISSUE_PROJECT")
    return (legacy_project,) if legacy_project else ()


def _normalize_auth_mode(raw: str | None) -> str:
    auth_mode = (raw or AUTH_MODE_COOKIE).strip().lower().replace("-", "_")
    if auth_mode == "basicwithcookies":
        auth_mode = AUTH_MODE_BASIC_WITH_COOKIES
    if auth_mode not in ALLOWED_AUTH_MODES:
        allowed = ", ".join(sorted(ALLOWED_AUTH_MODES))
        raise RuntimeError(f"JIRA_AUTH_MODE must be one of: {allowed}")
    return auth_mode


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_recovery_script_path() -> str:
    return str(_repo_root() / "scripts" / "jira_browser_recover.py")


def _default_profile_dir() -> str:
    return str(_repo_root() / "jira_browser_profile")


def _default_internal_cookie_storage_path() -> str:
    return str(_repo_root() / ".state" / "jira_cookie.json")


def _validate_settings(settings: Settings) -> None:
    has_basic = bool(settings.username and settings.password)
    has_cookie = bool(settings.cookie)
    has_token = bool(settings.token)
    has_any_auto = has_cookie or has_basic or has_token

    if settings.auth_mode == AUTH_MODE_COOKIE and not has_cookie:
        raise RuntimeError("JIRA_COOKIE is required when JIRA_AUTH_MODE=cookie")
    if settings.auth_mode in {AUTH_MODE_BASIC, AUTH_MODE_BASIC_WITH_COOKIES} and not has_basic:
        raise RuntimeError(
            "JIRA_USERNAME and JIRA_PASSWORD are required when JIRA_AUTH_MODE=basic/basic_with_cookies"
        )
    if settings.auth_mode == AUTH_MODE_BEARER and not (has_token or has_cookie):
        raise RuntimeError(
            "JIRA_BEARER_TOKEN (or legacy JIRA_TOKEN / JIRA_COOKIE fallback) is required when JIRA_AUTH_MODE=bearer"
        )
    if settings.auth_mode == AUTH_MODE_AUTO and not has_any_auto:
        raise RuntimeError(
            "JIRA_AUTH_MODE=auto requires at least one auth source: JIRA_COOKIE, JIRA_USERNAME+JIRA_PASSWORD, or JIRA_BEARER_TOKEN/JIRA_TOKEN"
        )
    if settings.browser_recovery_cooldown_minutes < 1:
        raise RuntimeError("JIRA_BROWSER_RECOVERY_COOLDOWN_MINUTES must be >= 1")
    if settings.enable_create_issue and not settings.create_issue_project_whitelist:
        raise RuntimeError(
            "JIRA_CREATE_ISSUE_PROJECT_WHITELIST is required when JIRA_ENABLE_CREATE_ISSUE=true"
        )


def load_settings() -> Settings:
    load_dotenv()

    base_url = _clean(os.getenv("JIRA_BASE_URL"))
    if not base_url:
        raise RuntimeError("JIRA_BASE_URL is required")

    api_version = int((_clean(os.getenv("JIRA_API_VERSION")) or "2"))
    if api_version not in {2, 3}:
        raise RuntimeError("JIRA_API_VERSION must be 2 or 3")

    timeout_sec = int((_clean(os.getenv("JIRA_TIMEOUT_SEC")) or "60"))
    default_limit = int((_clean(os.getenv("JIRA_DEFAULT_LIMIT")) or "50"))

    token = _clean(os.getenv("JIRA_BEARER_TOKEN")) or _clean(os.getenv("JIRA_TOKEN"))
    settings = Settings(
        base_url=base_url,
        api_version=api_version,
        auth_mode=_normalize_auth_mode(os.getenv("JIRA_AUTH_MODE")),
        cookie=_clean(os.getenv("JIRA_COOKIE")),
        username=_clean(os.getenv("JIRA_USERNAME")),
        password=_clean(os.getenv("JIRA_PASSWORD")),
        token=token,
        timeout_sec=max(5, min(timeout_sec, 300)),
        default_limit=max(1, min(default_limit, 200)),
        write_project_whitelist=_csv("JIRA_WRITE_PROJECT_WHITELIST"),
        write_issue_whitelist=_csv("JIRA_WRITE_ISSUE_WHITELIST"),
        enable_create_issue=_flag("JIRA_ENABLE_CREATE_ISSUE", default=False),
        create_issue_project_whitelist=_create_issue_project_whitelist(),
        enable_browser_recovery=_flag("JIRA_ENABLE_BROWSER_RECOVERY", default=False),
        browser_recovery_script_path=_clean(os.getenv("JIRA_BROWSER_RECOVERY_SCRIPT_PATH"))
        or _default_recovery_script_path(),
        browser_profile_dir=_clean(os.getenv("JIRA_BROWSER_PROFILE_DIR")) or _default_profile_dir(),
        internal_cookie_storage_path=_clean(os.getenv("JIRA_INTERNAL_COOKIE_STORAGE_PATH"))
        or _default_internal_cookie_storage_path(),
        browser_recovery_cooldown_minutes=int(
            (_clean(os.getenv("JIRA_BROWSER_RECOVERY_COOLDOWN_MINUTES")) or "60")
        ),
    )
    _validate_settings(settings)
    return settings
