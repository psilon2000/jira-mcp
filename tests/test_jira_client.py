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
        self.last_json_body: dict | None = None
        self.last_uploaded_file: dict[str, str | int] | None = None
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
        if path.endswith("/board/865/sprint") and self.jira_server.scenario == "board_sprints":
            return self._json(
                200,
                {
                    "maxResults": 2,
                    "startAt": 0,
                    "total": 1,
                    "isLast": True,
                    "values": [{"id": 456, "name": "AQ Sprint 1", "state": "active"}],
                },
            )
        if path.endswith("/issue/TEAM-1/transitions"):
            if self.jira_server.scenario == "basic_with_cookies" and cookie == "JSESSIONID=session-cookie":
                return self._json(200, {"transitions": [{"id": "1", "name": "Done"}]})
            return self._json(403, {"errorMessages": ["forbidden"]})
        if path.endswith("/issue/TEAM-1/attachments") and self.jira_server.scenario == "attachment_cookie_fallback":
            if cookie == "JSESSIONID=bad-cookie":
                return self._json(403, {"errorMessages": ["bad cookie"]})
            auth = self.headers.get("Authorization")
            if auth == self.jira_server.basic_header:
                return self._json(200, [{"id": "20002", "filename": "test.sql"}])
            return self._json(403, {"errorMessages": ["forbidden"]})
        return self._json(404, {"errorMessages": ["not found"]})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        if path.endswith("/issue/TEAM-1/attachments") and self.jira_server.scenario == "add_attachment":
            self.jira_server.last_uploaded_file = {
                "content_type": self.headers.get("Content-Type", ""),
                "x_atlassian_token": self.headers.get("X-Atlassian-Token", ""),
                "size": len(raw_body),
                "body": raw_body.decode("utf-8", errors="ignore"),
            }
            return self._json(
                200,
                [
                    {
                        "id": "20001",
                        "filename": "test.sql",
                        "size": len(raw_body),
                    }
                ],
            )
        if path.endswith("/issue/TEAM-1/attachments") and self.jira_server.scenario == "attachment_cookie_fallback":
            self.jira_server.last_uploaded_file = {
                "content_type": self.headers.get("Content-Type", ""),
                "x_atlassian_token": self.headers.get("X-Atlassian-Token", ""),
                "size": len(raw_body),
                "body": raw_body.decode("utf-8", errors="ignore"),
            }
            if self.headers.get("Cookie") == "JSESSIONID=bad-cookie":
                return self._json(403, {"errorMessages": ["bad cookie"]})
            if self.headers.get("Authorization") == self.jira_server.basic_header:
                return self._json(200, [{"id": "20002", "filename": "test.sql"}])
            return self._json(403, {"errorMessages": ["forbidden"]})
        self.jira_server.last_json_body = json.loads(raw_body.decode() or "{}")

        if path.endswith("/issue") and self.jira_server.scenario == "create_issue":
            return self._json(
                201,
                {
                    "id": "10001",
                    "key": "TEAM-42",
                    "self": "https://jira.example.local/rest/api/2/issue/10001",
                },
            )
        if path.endswith("/sprint/456/issue") and self.jira_server.scenario == "sprint_write":
            return self._json(201, {})
        if path.endswith("/backlog/issue") and self.jira_server.scenario == "sprint_write":
            return self._json(204, {})

        return self._json(404, {"errorMessages": ["not found"]})

    def do_PUT(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        self.jira_server.last_json_body = json.loads(raw_body.decode() or "{}")

        if path.endswith("/issue/TEAM-1") and self.jira_server.scenario == "update_issue":
            self.send_response(204)
            self.end_headers()
            return
        if path.endswith("/issue/TEAM-1/comment/123") and self.jira_server.scenario == "update_comment":
            self.send_response(204)
            self.end_headers()
            return

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
        "write_sprint_whitelist": (),
        "enable_create_issue": False,
        "create_issue_project_whitelist": (),
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

    def test_create_issue_posts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("create_issue") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.create_issue(
                project_key="TEAM",
                summary="Ship create tool",
                issue_type="Task",
                description="Add Jira create issue tool",
                fields={"priority": {"name": "High"}},
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["issue_key"], "TEAM-42")
            self.assertEqual(
                server.last_json_body,
                {
                    "fields": {
                        "priority": {"name": "High"},
                        "project": {"key": "TEAM"},
                        "issuetype": {"name": "Task"},
                        "summary": "Ship create tool",
                        "description": "Add Jira create issue tool",
                    }
                },
            )

    def test_create_issue_accepts_issue_type_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("create_issue") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            client.create_issue(
                project_key="AQ",
                summary="Ship create tool",
                issue_type="10006",
                description=None,
                fields=None,
            )

            self.assertEqual(
                server.last_json_body,
                {
                    "fields": {
                        "project": {"key": "AQ"},
                        "issuetype": {"id": "10006"},
                        "summary": "Ship create tool",
                    }
                },
            )

    def test_update_issue_puts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("update_issue") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.update_issue(
                issue_key="TEAM-1",
                fields={"description": "Updated description", "priority": {"name": "High"}},
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["updated_fields"], ["description", "priority"])
            self.assertEqual(
                server.last_json_body,
                {
                    "fields": {
                        "description": "Updated description",
                        "priority": {"name": "High"},
                    }
                },
            )

    def test_update_comment_puts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("update_comment") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.update_comment(issue_key="TEAM-1", comment_id="123", comment="Updated comment")

            self.assertEqual(result, {"status": "ok", "issue_key": "TEAM-1", "comment_id": "123"})
            self.assertEqual(server.last_json_body, {"body": "Updated comment"})

    def test_list_board_sprints_uses_agile_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("board_sprints") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.list_board_sprints(board_id=865, state="active", limit=10, start_at=0)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["board_id"], 865)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["sprints"][0]["id"], 456)

    def test_get_current_board_sprint_prefers_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("board_sprints") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            with unittest.mock.patch.object(client, "list_board_sprints") as list_sprints:
                list_sprints.side_effect = [
                    {"sprints": [{"id": 111, "name": "Active Sprint", "state": "active"}]},
                ]
                result = client.get_current_board_sprint(board_id=865)

            self.assertEqual(result["selection"], "active")
            self.assertEqual(result["sprint"]["id"], 111)

    def test_get_current_board_sprint_falls_back_to_future(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("board_sprints") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            with unittest.mock.patch.object(client, "list_board_sprints") as list_sprints:
                list_sprints.side_effect = [
                    {"sprints": []},
                    {"sprints": [{"id": 293, "name": "SCRUM Спринт 63", "state": "future"}, {"id": 294, "name": "SCRUM Спринт 64", "state": "future"}]},
                ]
                result = client.get_current_board_sprint(board_id=865)

            self.assertEqual(result["selection"], "future")
            self.assertEqual(result["sprint"]["id"], 293)

    def test_add_issues_to_sprint_posts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("sprint_write") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.add_issues_to_sprint(sprint_id=456, issue_keys=["AQ-1", "AQ-2"])

            self.assertEqual(result["status"], "ok")
            self.assertEqual(server.last_json_body, {"issues": ["AQ-1", "AQ-2"]})

    def test_remove_issues_from_sprint_posts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("sprint_write") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.remove_issues_from_sprint(issue_keys=["AQ-1"])

            self.assertEqual(result["status"], "ok")
            self.assertEqual(server.last_json_body, {"issues": ["AQ-1"]})

    def test_add_attachment_posts_multipart_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("add_attachment") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)
            sql_file = Path(tmpdir) / "test.sql"
            sql_file.write_text("select 1;\n")

            result = client.add_attachment(issue_key="TEAM-1", file_path=str(sql_file))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["attachment_id"], "20001")
            self.assertEqual(result["filename"], "test.sql")
            self.assertIsNotNone(server.last_uploaded_file)
            assert server.last_uploaded_file is not None
            self.assertIn("multipart/form-data", str(server.last_uploaded_file["content_type"]))
            self.assertEqual(server.last_uploaded_file["x_atlassian_token"], "no-check")
            self.assertIn('filename="test.sql"', str(server.last_uploaded_file["body"]))

    def test_add_attachment_survives_auth_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("attachment_cookie_fallback") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)
            sql_file = Path(tmpdir) / "test.sql"
            sql_file.write_text("select 1;\n")

            result = client.add_attachment(issue_key="TEAM-1", file_path=str(sql_file))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["attachment_id"], "20002")
            self.assertIsNotNone(server.last_uploaded_file)
            assert server.last_uploaded_file is not None
            self.assertGreater(int(server.last_uploaded_file["size"]), 0)
            self.assertIn('filename="test.sql"', str(server.last_uploaded_file["body"]))
