from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from jira_mcp.config import AUTH_MODE_BASIC_WITH_COOKIES, load_settings


class ConfigTests(unittest.TestCase):
    def test_load_settings_accepts_basic_with_cookies(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "BasicWithCookies",
            "JIRA_USERNAME": "bot",
            "JIRA_PASSWORD": "secret",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual(settings.auth_mode, AUTH_MODE_BASIC_WITH_COOKIES)

    def test_load_settings_requires_credentials_for_auto(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "auto",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "requires at least one auth source"):
                load_settings()

    def test_load_settings_uses_defaults_for_recovery_paths(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertTrue(settings.browser_recovery_script_path.endswith("scripts/jira_browser_recover.py"))
        self.assertTrue(settings.internal_cookie_storage_path.endswith(".state/jira_cookie.json"))

    def test_load_settings_uses_cache_defaults(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertFalse(settings.enable_cache)
        self.assertTrue(settings.cache_path.endswith(".state/jira_cache.json"))
        self.assertEqual(settings.cache_ttl_seconds, 3600)
        self.assertEqual(settings.cache_max_entries, 1000)

    def test_load_settings_requires_create_project_when_enabled(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
            "JIRA_ENABLE_CREATE_ISSUE": "true",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "JIRA_CREATE_ISSUE_PROJECT_WHITELIST is required"):
                load_settings()

    def test_load_settings_normalizes_create_project_whitelist(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
            "JIRA_ENABLE_CREATE_ISSUE": "true",
            "JIRA_CREATE_ISSUE_PROJECT_WHITELIST": " team, aq ",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertTrue(settings.enable_create_issue)
        self.assertEqual(settings.create_issue_project_whitelist, ("TEAM", "AQ"))

    def test_load_settings_supports_legacy_create_project_env(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
            "JIRA_ENABLE_CREATE_ISSUE": "true",
            "JIRA_CREATE_ISSUE_PROJECT": " team ",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual(settings.create_issue_project_whitelist, ("TEAM",))

    def test_load_settings_normalizes_write_sprint_whitelist(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
            "JIRA_WRITE_SPRINT_WHITELIST": " 101, 202 ",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual(settings.write_sprint_whitelist, (101, 202))

    def test_load_settings_normalizes_write_board_whitelist(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
            "JIRA_WRITE_BOARD_WHITELIST": " 865, 866 ",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual(settings.write_board_whitelist, (865, 866))

    def test_load_settings_rejects_non_numeric_sprint_whitelist(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://jira.example.local",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_COOKIE": "JSESSIONID=value",
            "JIRA_WRITE_SPRINT_WHITELIST": "abc",
        }
        with patch("jira_mcp.config.load_dotenv", return_value=False), patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "only integers"):
                load_settings()
