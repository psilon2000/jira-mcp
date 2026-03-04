from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


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

    @property
    def rest_root(self) -> str:
        return f"{self.base_url.rstrip('/')}/rest/api/{self.api_version}"


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def load_settings() -> Settings:
    load_dotenv()

    base_url = os.getenv("JIRA_BASE_URL", "").strip()
    if not base_url:
        raise RuntimeError("JIRA_BASE_URL is required")

    auth_mode = os.getenv("JIRA_AUTH_MODE", "cookie").strip().lower() or "cookie"
    if auth_mode not in {"cookie", "basic", "bearer", "auto"}:
        raise RuntimeError("JIRA_AUTH_MODE must be one of: cookie, basic, bearer, auto")

    api_version = int(os.getenv("JIRA_API_VERSION", "2").strip() or "2")
    if api_version not in {2, 3}:
        raise RuntimeError("JIRA_API_VERSION must be 2 or 3")

    timeout_sec = int(os.getenv("JIRA_TIMEOUT_SEC", "60").strip() or "60")
    default_limit = int(os.getenv("JIRA_DEFAULT_LIMIT", "50").strip() or "50")

    return Settings(
        base_url=base_url,
        api_version=api_version,
        auth_mode=auth_mode,
        cookie=os.getenv("JIRA_COOKIE", "").strip() or None,
        username=os.getenv("JIRA_USERNAME", "").strip() or None,
        password=os.getenv("JIRA_PASSWORD", "").strip() or None,
        token=os.getenv("JIRA_TOKEN", "").strip() or None,
        timeout_sec=max(5, min(timeout_sec, 300)),
        default_limit=max(1, min(default_limit, 200)),
        write_project_whitelist=_csv("JIRA_WRITE_PROJECT_WHITELIST"),
        write_issue_whitelist=_csv("JIRA_WRITE_ISSUE_WHITELIST"),
    )
