from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jira_mcp.auth_state import (
    COOKIE_SOURCE_CONFIGURED,
    COOKIE_SOURCE_INTERNAL,
    COOKIE_SOURCE_NONE,
    JiraRuntimeAuthState,
)
from jira_mcp.config import Settings


def make_settings(storage_path: str, cookie: str | None = "JSESSIONID=configured") -> Settings:
    return Settings(
        base_url="https://jira.example.local",
        api_version=2,
        auth_mode="auto",
        cookie=cookie,
        username="bot",
        password="secret",
        token=None,
        timeout_sec=30,
        default_limit=20,
        write_project_whitelist=(),
        write_issue_whitelist=(),
        write_sprint_whitelist=(),
        enable_create_issue=False,
        create_issue_project_whitelist=(),
        enable_browser_recovery=False,
        browser_recovery_script_path="/tmp/helper.py",
        browser_profile_dir="/tmp/profile",
        internal_cookie_storage_path=storage_path,
        browser_recovery_cooldown_minutes=60,
    )


class AuthStateTests(unittest.TestCase):
    def test_promote_internal_cookie_persists_and_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = str(Path(tmpdir) / "jira_cookie.json")
            state = JiraRuntimeAuthState(make_settings(storage))
            state.promote_internal_cookie("JSESSIONID=internal")

            reloaded = JiraRuntimeAuthState(make_settings(storage))
            self.assertEqual(reloaded.get_cookie(), "JSESSIONID=internal")
            self.assertEqual(reloaded.get_active_source(), COOKIE_SOURCE_INTERNAL)

    def test_mark_active_cookie_invalid_falls_back_to_configured_then_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = str(Path(tmpdir) / "jira_cookie.json")
            state = JiraRuntimeAuthState(make_settings(storage))
            state.promote_internal_cookie("JSESSIONID=internal")

            self.assertTrue(state.mark_active_cookie_invalid())
            self.assertEqual(state.get_cookie(), "JSESSIONID=configured")
            self.assertEqual(state.get_active_source(), COOKIE_SOURCE_CONFIGURED)

            self.assertFalse(state.mark_active_cookie_invalid())
            self.assertIsNone(state.get_cookie())
            self.assertEqual(state.get_active_source(), COOKIE_SOURCE_NONE)
