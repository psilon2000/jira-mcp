from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from jira_mcp.auth_state import JiraRuntimeAuthState
from jira_mcp.config import Settings
from jira_mcp.jira_client import JiraClient
from jira_mcp.recovery import BrowserRecoveryService


class JiraTestServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.basic_header = "Basic " + base64.b64encode(b"bot:secret").decode()
        self.last_cookie_header: str | None = None
        super().__init__(("127.0.0.1", 0), JiraHandler)


class JiraHandler(BaseHTTPRequestHandler):
    @property
    def jira_server(self) -> JiraTestServer:
        return cast(JiraTestServer, self.server)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        cookie = self.headers.get("Cookie")
        auth = self.headers.get("Authorization")
        self.jira_server.last_cookie_header = cookie

        if path.endswith("/myself"):
            return self._handle_myself(cookie, auth)
        if path.endswith("/issue/TEAM-1/transitions"):
            if self.jira_server.scenario == "basic_with_cookies" and cookie == "JSESSIONID=session-cookie":
                return self._json(200, {"transitions": [{"id": "1", "name": "Done"}]})
            return self._json(403, {"errorMessages": ["forbidden"]})
        return self._json(404, {"errorMessages": ["not found"]})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_myself(self, cookie: str | None, auth: str | None) -> None:
        if self.jira_server.scenario == "cookie_success":
            if cookie == "JSESSIONID=good-cookie":
                return self._json(200, {"name": "bot", "displayName": "Bot", "active": True})
            return self._json(403, {"errorMessages": ["bad cookie"]})

        if self.jira_server.scenario == "fallback_basic_with_cookies":
            if cookie == "JSESSIONID=bad-cookie":
                return self._json(403, {"errorMessages": ["bad cookie"]})
            if auth == self.jira_server.basic_header:
                headers = {"Set-Cookie": "JSESSIONID=session-cookie; Path=/"}
                return self._json(200, {"name": "bot", "displayName": "Bot", "active": True}, headers=headers)
            return self._json(403, {"errorMessages": ["forbidden"]})

        if self.jira_server.scenario == "basic_with_cookies":
            if auth == self.jira_server.basic_header:
                headers = {"Set-Cookie": "JSESSIONID=session-cookie; Path=/"}
                return self._json(200, {"name": "bot", "displayName": "Bot", "active": True}, headers=headers)
            return self._json(403, {"errorMessages": ["forbidden"]})

        if self.jira_server.scenario == "recovery_success":
            if cookie == "JSESSIONID=recovered-cookie":
                return self._json(200, {"name": "bot", "displayName": "Bot", "active": True})
            return self._json(403, {"errorMessages": ["bad cookie"]})

        return self._json(500, {"errorMessages": ["unknown scenario"]})

    def _json(self, status: int, payload: dict, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


class JiraServerContext:
    def __init__(self, scenario: str):
        self.server = JiraTestServer(scenario)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> JiraTestServer:
        self.thread.start()
        return self.server

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def make_settings(base_url: str, storage_path: str, **overrides: object) -> Settings:
    values = {
        "base_url": base_url,
        "api_version": 2,
        "auth_mode": "auto",
        "cookie": "JSESSIONID=bad-cookie",
        "username": "bot",
        "password": "secret",
        "token": None,
        "timeout_sec": 5,
        "default_limit": 20,
        "write_project_whitelist": (),
        "write_issue_whitelist": (),
        "enable_browser_recovery": False,
        "browser_recovery_script_path": str(Path(storage_path).with_name("helper.py")),
        "browser_profile_dir": str(Path(storage_path).with_name("profile")),
        "internal_cookie_storage_path": storage_path,
        "browser_recovery_cooldown_minutes": 1,
    }
    values.update(overrides)
    return Settings(**values)


class JiraClientTests(unittest.TestCase):
    def test_auto_falls_back_to_basic_with_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("fallback_basic_with_cookies") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.auth_status()

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["auth"]["cookie_source"], "none")

    def test_basic_with_cookies_reuses_session_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("basic_with_cookies") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic_with_cookies",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            client.auth_status()
            transitions = client.list_transitions("TEAM-1")

            self.assertEqual(transitions["status"], "ok")
            self.assertEqual(server.last_cookie_header, "JSESSIONID=session-cookie")

    def test_recovery_updates_internal_cookie_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("recovery_success") as server:
            storage_path = str(Path(tmpdir) / "jira_cookie.json")
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "import json\n"
                "print(json.dumps({'success': True, 'details': 'ok', 'cookie': 'JSESSIONID=recovered-cookie'}))\n"
            )
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                storage_path,
                username=None,
                password=None,
                enable_browser_recovery=True,
                browser_recovery_script_path=str(helper_path),
            )
            state = JiraRuntimeAuthState(settings)
            recovery = BrowserRecoveryService(settings, state)
            client = JiraClient(settings, state, recovery)

            result = client.auth_status()

            self.assertEqual(result["status"], "ok")
            self.assertEqual(state.get_cookie(), "JSESSIONID=recovered-cookie")
            self.assertTrue(Path(storage_path).exists())

    def test_recovery_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("recovery_success") as server:
            storage_path = str(Path(tmpdir) / "jira_cookie.json")
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text("raise SystemExit(2)\n")
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                storage_path,
                username=None,
                password=None,
                enable_browser_recovery=True,
                browser_recovery_script_path=str(helper_path),
            )
            state = JiraRuntimeAuthState(settings)
            recovery = BrowserRecoveryService(settings, state)
            client = JiraClient(settings, state, recovery)

            with self.assertRaisesRegex(RuntimeError, "recovery="):
                client.auth_status()
