from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

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
        self.last_deleted_path: str | None = None
        self.last_uploaded_file: dict[str, str | int] | None = None
        self.request_log: list[str] = []
        self.delete_attempts: list[tuple[str | None, str | None]] = []
        super().__init__(("127.0.0.1", 0), JiraHandler)


class JiraHandler(BaseHTTPRequestHandler):
    @property
    def jira_server(self) -> JiraTestServer:
        return cast(JiraTestServer, self.server)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        query = parse_qs(urlsplit(self.path).query)
        self.jira_server.request_log.append(self.path)
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
        if path.endswith("/sprint/456") and self.jira_server.scenario == "sprint_manage":
            return self._json(
                200,
                {
                    "id": 456,
                    "self": "https://jira.example.local/rest/agile/1.0/sprint/456",
                    "state": "future",
                    "name": "Old sprint",
                    "startDate": "2026-05-18T09:00:00.000+03:00",
                    "endDate": "2026-05-29T21:00:00.000+03:00",
                    "originBoardId": 865,
                    "goal": "Old goal",
                },
            )
        if path.endswith("/issue/TEAM-1/transitions"):
            if self.jira_server.scenario == "basic_with_cookies" and cookie == "JSESSIONID=session-cookie":
                return self._json(200, {"transitions": [{"id": "1", "name": "Done"}]})
            return self._json(403, {"errorMessages": ["forbidden"]})
        if path.endswith("/issue/TEAM-1") and self.jira_server.scenario == "issue_cache":
            if "fields=updated" in self.path:
                return self._json(200, {"key": "TEAM-1", "fields": {"updated": "2026-07-03T10:00:00.000+0000"}})
            return self._json(
                200,
                {
                    "key": "TEAM-1",
                    "fields": {
                        "summary": "Cacheable issue",
                        "description": "Local buffer search target",
                        "updated": "2026-07-03T10:00:00.000+0000",
                    },
                },
            )
        if path.endswith("/search") and self.jira_server.scenario == "search_cache":
            start_at = int(query.get("startAt", ["0"])[0])
            issue_number = start_at + 1
            return self._json(
                200,
                {
                    "startAt": start_at,
                    "maxResults": int(query.get("maxResults", ["10"])[0]),
                    "total": 26,
                    "issues": [
                        {
                            "key": f"TEAM-{issue_number}",
                            "fields": {
                                "summary": "Cacheable issue",
                                "description": "Local buffer search target",
                                "updated": "2026-07-03T10:00:00.000+0000",
                            },
                        }
                    ],
                },
            )
        if path.endswith("/issue/TEAM-1/worklog") and self.jira_server.scenario == "extended_read_tools":
            start_at = int(query.get("startAt", ["0"])[0])
            return self._json(
                200,
                {
                    "startAt": start_at,
                    "maxResults": int(query.get("maxResults", ["20"])[0]),
                    "total": 2,
                    "worklogs": [] if start_at >= 2 else [{"id": "501", "timeSpentSeconds": 3600}],
                },
            )
        if path.endswith("/issue/TEAM-1/changelog") and self.jira_server.scenario == "extended_read_tools":
            start_at = int(query.get("startAt", ["0"])[0])
            return self._json(
                200,
                {
                    "startAt": start_at,
                    "maxResults": int(query.get("maxResults", ["20"])[0]),
                    "total": 1,
                    "isLast": True,
                    "values": [] if start_at >= 1 else [{"id": "601", "items": [{"field": "status"}]}],
                },
            )
        if path.endswith("/issue/TEAM-1/changelog") and self.jira_server.scenario == "changelog_expand_fallback":
            return self._json(404, {"errorMessages": ["not found"]})
        if path.endswith("/field") and self.jira_server.scenario == "extended_read_tools":
            return self._json_list(
                200,
                [
                    {"id": "summary", "name": "Summary", "custom": False, "clauseNames": ["summary"]},
                    {
                        "id": "customfield_10008",
                        "name": "Epic Link",
                        "custom": True,
                        "clauseNames": ["Epic Link", "cf[10008]"],
                    },
                ],
            )
        if path.endswith("/issue/TEAM-1/editmeta") and self.jira_server.scenario == "extended_read_tools":
            return self._json(
                200,
                {
                    "fields": {
                        "summary": {"required": True, "name": "Summary"},
                        "customfield_10008": {
                            "required": False,
                            "name": "Epic Link",
                            "allowedValues": [{"key": "TEAM-10"}],
                        },
                    }
                },
            )
        if path.endswith("/project/TEAM") and self.jira_server.scenario == "extended_read_tools":
            return self._json(200, {"id": "10000", "key": "TEAM", "name": "Team Project"})
        if path.endswith("/board") and self.jira_server.scenario == "extended_read_tools":
            return self._json(
                200,
                {
                    "startAt": int(query.get("startAt", ["0"])[0]),
                    "maxResults": int(query.get("maxResults", ["20"])[0]),
                    "total": 1,
                    "isLast": True,
                    "values": [{"id": 865, "name": "TEAM board", "type": "scrum"}],
                },
            )
        if path.endswith("/board/865/configuration") and self.jira_server.scenario == "extended_read_tools":
            return self._json(
                200,
                {"id": 865, "name": "TEAM board", "filter": {"id": "20001", "self": "filter/20001"}},
            )
        if path.endswith("/issue/TEAM-1") and self.jira_server.scenario in {
            "delete_attachment",
            "delete_attachment_missing",
            "delete_attachment_moved_issue",
            "delete_attachment_forbidden",
        }:
            attachments = []
            if self.jira_server.scenario != "delete_attachment_missing":
                attachments = [{"id": "20001", "filename": "obsolete.sql"}]
            issue_key = "OTHER-1" if self.jira_server.scenario == "delete_attachment_moved_issue" else "TEAM-1"
            return self._json(200, {"key": issue_key, "fields": {"attachment": attachments}})
        if path.endswith("/issue/TEAM-1") and self.jira_server.scenario == "changelog_expand_fallback":
            return self._json(
                200,
                {
                    "key": "TEAM-1",
                    "changelog": {
                        "startAt": 0,
                        "maxResults": 20,
                        "total": 1,
                        "histories": [{"id": "602", "items": [{"field": "status"}]}],
                    },
                },
            )
        if path.endswith("/issueLink/12345") and self.jira_server.scenario == "delete_issue_link":
            return self._json(
                200,
                {
                    "id": "12345",
                    "type": {"name": "Blocks"},
                    "inwardIssue": {"key": "PV-1"},
                    "outwardIssue": {"key": "FRMM-1"},
                },
            )
        if path.endswith("/issue/TEAM-1") and self.jira_server.scenario == "download_attachment_by_filename":
            return self._json(
                200,
                {
                    "fields": {
                        "attachment": [
                            {
                                "id": "20001",
                                "filename": "test.sql",
                                "mimeType": "application/sql",
                                "content": self._attachment_content_url("20001", "test.sql"),
                            }
                        ]
                    }
                },
            )
        if path.endswith("/attachment/20001") and self.jira_server.scenario in {
            "download_attachment",
            "download_attachment_cookie_fallback",
        }:
            if self.jira_server.scenario == "download_attachment_cookie_fallback":
                if cookie == "JSESSIONID=bad-cookie":
                    return self._json(403, {"errorMessages": ["bad cookie"]})
                if auth != self.jira_server.basic_header:
                    return self._json(403, {"errorMessages": ["forbidden"]})
            return self._json(
                200,
                {
                    "id": "20001",
                    "filename": "test.sql",
                    "mimeType": "application/sql",
                    "content": self._attachment_content_url("20001", "test.sql"),
                },
            )
        if path.endswith("/issue/TEAM-1/attachments") and self.jira_server.scenario == "attachment_cookie_fallback":
            if cookie == "JSESSIONID=bad-cookie":
                return self._json(403, {"errorMessages": ["bad cookie"]})
            auth = self.headers.get("Authorization")
            if auth == self.jira_server.basic_header:
                return self._json(200, [{"id": "20002", "filename": "test.sql"}])
            return self._json(403, {"errorMessages": ["forbidden"]})
        if path.endswith("/secure/attachment/20001/test.sql") and self.jira_server.scenario in {
            "download_attachment",
            "download_attachment_by_filename",
            "download_attachment_cookie_fallback",
        }:
            if self.jira_server.scenario == "download_attachment_cookie_fallback":
                if cookie == "JSESSIONID=bad-cookie":
                    self.send_response(302)
                    self.send_header("Location", "/login.jsp?permissionViolation=true")
                    self.end_headers()
                    return
                if auth != self.jira_server.basic_header:
                    return self._json(403, {"errorMessages": ["forbidden"]})
            return self._bytes(200, b"select 1;\n", content_type="application/sql")
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
        if path.endswith("/sprint") and self.jira_server.scenario == "sprint_manage":
            return self._json(
                201,
                {
                    "id": 789,
                    "state": "future",
                    "name": self.jira_server.last_json_body.get("name"),
                    "originBoardId": self.jira_server.last_json_body.get("originBoardId"),
                    "startDate": self.jira_server.last_json_body.get("startDate"),
                    "endDate": self.jira_server.last_json_body.get("endDate"),
                    "goal": self.jira_server.last_json_body.get("goal"),
                },
            )
        if path.endswith("/issueLink") and self.jira_server.scenario == "link_issues":
            self.send_response(201)
            self.end_headers()
            return
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
        if path.endswith("/sprint/456") and self.jira_server.scenario == "sprint_manage":
            body = dict(self.jira_server.last_json_body or {})
            body.setdefault("id", 456)
            return self._json(200, body)

        return self._json(404, {"errorMessages": ["not found"]})

    def do_DELETE(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        self.jira_server.request_log.append(self.path)
        self.jira_server.delete_attempts.append(
            (self.headers.get("Cookie"), self.headers.get("Authorization"))
        )
        if path.endswith("/issue/TEAM-1/comment/123") and self.jira_server.scenario == "delete_comment":
            self.jira_server.last_deleted_path = path
            self.send_response(204)
            self.end_headers()
            return
        if path.endswith("/issueLink/12345") and self.jira_server.scenario == "delete_issue_link":
            self.jira_server.last_deleted_path = path
            self.send_response(204)
            self.end_headers()
            return
        if path.endswith("/attachment/20001") and self.jira_server.scenario == "delete_attachment":
            self.jira_server.last_deleted_path = path
            self.send_response(204)
            self.end_headers()
            return
        if path.endswith("/attachment/20001") and self.jira_server.scenario == "delete_attachment_forbidden":
            if self.headers.get("Cookie") == "JSESSIONID=bad-cookie":
                return self._json(403, {"errorMessages": ["delete attachment forbidden"]})
            if self.headers.get("Authorization") == self.jira_server.basic_header:
                self.jira_server.last_deleted_path = path
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

    def _json_list(self, status: int, payload: list[dict]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _attachment_content_url(self, attachment_id: str, filename: str) -> str:
        return f"http://127.0.0.1:{self.jira_server.server_address[1]}/secure/attachment/{attachment_id}/{filename}"


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
        "write_board_whitelist": (),
        "enable_create_issue": False,
        "create_issue_project_whitelist": (),
        "enable_browser_recovery": False,
        "browser_recovery_script_path": str(Path(storage_path).with_name("helper.py")),
        "browser_profile_dir": str(Path(storage_path).with_name("profile")),
        "internal_cookie_storage_path": storage_path,
        "browser_recovery_cooldown_minutes": 1,
        "enable_cache": False,
        "cache_path": str(Path(storage_path).with_name("jira_cache.json")),
        "cache_ttl_seconds": 3600,
        "cache_max_entries": 1000,
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

    def test_get_issue_uses_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("issue_cache") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
                enable_cache=True,
                cache_path=str(Path(tmpdir) / "jira_cache.json"),
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            first = client.get_issue(issue_key="TEAM-1", fields=None, expand=None)
            second = client.get_issue(issue_key="TEAM-1", fields=None, expand=None)

            self.assertEqual(first["cache"], {"enabled": True, "hit": False})
            self.assertTrue(second["cache"]["hit"])
            self.assertEqual(second["issue"]["fields"]["summary"], "Cacheable issue")
            self.assertEqual(server.request_log.count("/rest/api/2/issue/TEAM-1"), 1)

    def test_get_issue_revalidates_stale_cache_with_updated_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("issue_cache") as server:
            cache_path = Path(tmpdir) / "jira_cache.json"
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
                enable_cache=True,
                cache_path=str(cache_path),
                cache_ttl_seconds=1,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            client.get_issue(issue_key="TEAM-1", fields=None, expand=None)
            data = json.loads(cache_path.read_text())
            for entry in data["issues"].values():
                entry["saved_at"] = 0
            cache_path.write_text(json.dumps(data))

            result = client.get_issue(issue_key="TEAM-1", fields=None, expand=None)

            self.assertTrue(result["cache"]["hit"])
            self.assertTrue(result["cache"]["revalidated"])
            self.assertEqual(server.request_log.count("/rest/api/2/issue/TEAM-1"), 1)
            self.assertEqual(server.request_log.count("/rest/api/2/issue/TEAM-1?fields=updated"), 1)

    def test_search_issues_and_cached_text_search_use_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("search_cache") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
                enable_cache=True,
                cache_path=str(Path(tmpdir) / "jira_cache.json"),
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            first = client.search_issues(
                jql="project = TEAM",
                fields=["summary", "description", "updated"],
                limit=10,
            )
            second = client.search_issues(
                jql="project = TEAM",
                fields=["summary", "description", "updated"],
                limit=10,
            )
            local = client.search_cached_issues(query="buffer target", limit=10)

            self.assertEqual(first["cache"], {"enabled": True, "hit": False})
            self.assertTrue(second["cache"]["hit"])
            self.assertEqual(local["count"], 1)
            self.assertEqual(local["issues"][0]["key"], "TEAM-1")
            self.assertEqual(sum(1 for path in server.request_log if path.startswith("/rest/api/2/search?")), 1)

    def test_search_issues_keeps_cache_pages_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("search_cache") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
                enable_cache=True,
                cache_path=str(Path(tmpdir) / "jira_cache.json"),
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            first_page = client.search_issues(jql="project = TEAM", fields=["summary"], limit=25)
            second_page = client.search_issues(jql="project = TEAM", fields=["summary"], limit=25, start_at=25)
            second_page_cached = client.search_issues(
                jql="project = TEAM",
                fields=["summary"],
                limit=25,
                start_at=25,
            )

            self.assertEqual(first_page["issues"][0]["key"], "TEAM-1")
            self.assertEqual(second_page["issues"][0]["key"], "TEAM-26")
            self.assertEqual(second_page["start_at"], 25)
            self.assertTrue(second_page["is_last"])
            self.assertTrue(second_page_cached["cache"]["hit"])
            requests = [path for path in server.request_log if path.startswith("/rest/api/2/search?")]
            self.assertEqual(len(requests), 2)
            self.assertTrue(any("startAt=25" in path for path in requests))

    def test_list_issue_worklogs_returns_page_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("extended_read_tools") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            result = client.list_issue_worklogs(issue_key="TEAM-1", limit=1, start_at=1)

            self.assertEqual(result["count"], 1)
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["start_at"], 1)
            self.assertTrue(result["is_last"])
            self.assertEqual(result["worklogs"][0]["id"], "501")
            self.assertIn("startAt=1&maxResults=1", server.request_log[-1])

            empty = client.list_issue_worklogs(issue_key="TEAM-1", limit=10, start_at=2)
            self.assertEqual(empty["count"], 0)
            self.assertTrue(empty["is_last"])

    def test_get_issue_changelog_normalizes_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("extended_read_tools") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            result = client.get_issue_changelog(issue_key="TEAM-1", limit=10, start_at=0)

            self.assertEqual(result["count"], 1)
            self.assertTrue(result["is_last"])
            self.assertEqual(result["source"], "endpoint")
            self.assertEqual(result["histories"][0]["id"], "601")

            empty = client.get_issue_changelog(issue_key="TEAM-1", limit=10, start_at=1)
            self.assertEqual(empty["count"], 0)
            self.assertTrue(empty["is_last"])

    def test_get_issue_changelog_falls_back_to_issue_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("changelog_expand_fallback") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            result = client.get_issue_changelog(issue_key="TEAM-1", limit=20, start_at=0)

            self.assertEqual(result["source"], "issue_expand")
            self.assertEqual(result["histories"][0]["id"], "602")
            self.assertTrue(any("fields=%2Anone&expand=changelog" in path for path in server.request_log))

            with self.assertRaisesRegex(ValueError, "only the first changelog page"):
                client.get_issue_changelog(issue_key="TEAM-1", limit=20, start_at=1)

    def test_list_fields_filters_query_and_custom_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("extended_read_tools") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            result = client.list_fields(query="epic", custom_only=True)

            self.assertEqual(result["count"], 1)
            self.assertEqual(result["fields"][0]["id"], "customfield_10008")

    def test_get_issue_edit_metadata_filters_field_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("extended_read_tools") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            result = client.get_issue_edit_metadata(issue_key="TEAM-1", field_ids=["customfield_10008"])
            all_fields = client.get_issue_edit_metadata(issue_key="TEAM-1")
            missing = client.get_issue_edit_metadata(issue_key="TEAM-1", field_ids=["customfield_99999"])

            self.assertEqual(result["count"], 1)
            self.assertEqual(list(result["fields"]), ["customfield_10008"])
            self.assertEqual(result["fields"]["customfield_10008"]["allowedValues"][0]["key"], "TEAM-10")
            self.assertEqual(all_fields["count"], 2)
            self.assertEqual(missing["fields"], {})

    def test_get_project_passes_expand(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("extended_read_tools") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            result = client.get_project(project_key="TEAM", expand=["description", "lead"])

            self.assertEqual(result["project"]["name"], "Team Project")
            self.assertIn("expand=description%2Clead", server.request_log[-1])

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

    def test_link_issues_posts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("link_issues") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.link_issues(
                source_issue_key="PV-1",
                target_issue_key="FRMM-1",
                link_type="Relates",
                comment="Frontend task for schema update",
            )

            self.assertEqual(
                result,
                {
                    "status": "ok",
                    "source_issue_key": "PV-1",
                    "target_issue_key": "FRMM-1",
                    "link_type": "Relates",
                },
            )
            self.assertEqual(
                server.last_json_body,
                {
                    "type": {"name": "Relates"},
                    "outwardIssue": {"key": "FRMM-1"},
                    "inwardIssue": {"key": "PV-1"},
                    "comment": {"body": "Frontend task for schema update"},
                },
            )

    def test_delete_issue_link_deletes_by_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("delete_issue_link") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.delete_issue_link(
                link_id="12345",
                source_issue_key="PV-1",
                target_issue_key="FRMM-1",
            )

            self.assertEqual(
                result,
                {
                    "status": "ok",
                    "link_id": "12345",
                    "source_issue_key": "PV-1",
                    "target_issue_key": "FRMM-1",
                    "link": {
                        "id": "12345",
                        "type": {"name": "Blocks"},
                        "inwardIssue": {"key": "PV-1"},
                        "outwardIssue": {"key": "FRMM-1"},
                    },
                },
            )
            self.assertEqual(server.last_deleted_path, "/rest/api/2/issueLink/12345")

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

    def test_delete_comment_deletes_by_issue_and_comment_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("delete_comment") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.delete_comment(issue_key="TEAM-1", comment_id="123")

            self.assertEqual(result, {"status": "ok", "issue_key": "TEAM-1", "comment_id": "123"})
            self.assertEqual(server.last_deleted_path, "/rest/api/2/issue/TEAM-1/comment/123")

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

    def test_list_boards_and_get_configuration_use_agile_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("extended_read_tools") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            boards = client.list_boards(
                project_key_or_id="TEAM",
                name="TEAM board",
                board_type="scrum",
                limit=10,
                start_at=0,
            )
            configuration = client.get_board_configuration(board_id=865)
            unfiltered = client.list_boards(
                project_key_or_id=None,
                name=None,
                board_type=None,
                limit=10,
                start_at=0,
            )

            self.assertEqual(boards["boards"][0]["id"], 865)
            self.assertTrue(boards["is_last"])
            self.assertEqual(configuration["configuration"]["filter"]["id"], "20001")
            self.assertEqual(unfiltered["count"], 1)
            board_request = next(path for path in server.request_log if path.startswith("/rest/agile/1.0/board?"))
            self.assertIn("projectKeyOrId=TEAM", board_request)
            self.assertIn("name=TEAM+board", board_request)
            self.assertIn("type=scrum", board_request)
            unfiltered_request = [
                path for path in server.request_log if path.startswith("/rest/agile/1.0/board?")
            ][-1]
            self.assertNotIn("projectKeyOrId", unfiltered_request)
            self.assertNotIn("name=", unfiltered_request)
            self.assertNotIn("type=", unfiltered_request)

    def test_create_sprint_posts_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("sprint_manage") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.create_sprint(
                board_id=865,
                name="SCRUM Sprint 68",
                start_date="2026-06-01T09:00:00.000+03:00",
                end_date="2026-06-12T21:00:00.000+03:00",
                goal="Digital ruble and acquiring",
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["sprint_id"], 789)
            self.assertEqual(
                server.last_json_body,
                {
                    "name": "SCRUM Sprint 68",
                    "originBoardId": 865,
                    "startDate": "2026-06-01T09:00:00.000+03:00",
                    "endDate": "2026-06-12T21:00:00.000+03:00",
                    "goal": "Digital ruble and acquiring",
                },
            )

    def test_update_sprint_merges_current_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("sprint_manage") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.update_sprint(
                sprint_id=456,
                state="active",
                start_date="2026-06-01T09:00:00.000+03:00",
                end_date="2026-06-12T21:00:00.000+03:00",
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                server.last_json_body,
                {
                    "id": 456,
                    "self": "https://jira.example.local/rest/agile/1.0/sprint/456",
                    "state": "active",
                    "name": "Old sprint",
                    "startDate": "2026-06-01T09:00:00.000+03:00",
                    "endDate": "2026-06-12T21:00:00.000+03:00",
                    "originBoardId": 865,
                    "goal": "Old goal",
                },
            )

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

    def test_delete_attachment_verifies_issue_before_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("delete_attachment") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            with unittest.mock.patch.object(client, "_invalidate_issue_cache") as invalidate:
                result = client.delete_attachment(issue_key="TEAM-1", attachment_id="20001")

            self.assertEqual(result["filename"], "obsolete.sql")
            self.assertEqual(server.last_deleted_path, "/rest/api/2/attachment/20001")
            self.assertTrue(any("/issue/TEAM-1?fields=attachment" in path for path in server.request_log))
            invalidate.assert_called_once_with("TEAM-1")

    def test_delete_attachment_rejects_attachment_from_another_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("delete_attachment_missing") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            with self.assertRaisesRegex(ValueError, "not found in issue"):
                client.delete_attachment(issue_key="TEAM-1", attachment_id="20001")

            self.assertIsNone(server.last_deleted_path)

    def test_delete_attachment_rejects_issue_key_resolution_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("delete_attachment_moved_issue") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            with self.assertRaisesRegex(ValueError, "resolved to 'OTHER-1'"):
                client.delete_attachment(issue_key="TEAM-1", attachment_id="20001")

            self.assertIsNone(server.last_deleted_path)

    def test_delete_attachment_does_not_escalate_on_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("delete_attachment_forbidden") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
            )
            client = JiraClient(settings, JiraRuntimeAuthState(settings))

            with self.assertRaisesRegex(RuntimeError, "403 DELETE /attachment/20001"):
                client.delete_attachment(issue_key="TEAM-1", attachment_id="20001")

            self.assertEqual(server.delete_attempts, [("JSESSIONID=bad-cookie", None)])
            self.assertIsNone(server.last_deleted_path)

    def test_download_attachment_by_id_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("download_attachment") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.download_attachment(
                attachment_id="20001",
                issue_key=None,
                filename=None,
                output_dir=tmpdir,
                overwrite=False,
            )

            saved_path = Path(result["saved_path"])
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["attachment_id"], "20001")
            self.assertEqual(result["filename"], "test.sql")
            self.assertEqual(saved_path.read_text(), "select 1;\n")

    def test_download_attachment_by_filename_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("download_attachment_by_filename") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
                auth_mode="basic",
                cookie=None,
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.download_attachment(
                attachment_id=None,
                issue_key="TEAM-1",
                filename="test.sql",
                output_dir=tmpdir,
                overwrite=False,
            )

            self.assertEqual(Path(result["saved_path"]).read_text(), "select 1;\n")

    def test_download_attachment_survives_login_redirect_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, JiraServerContext("download_attachment_cookie_fallback") as server:
            settings = make_settings(
                f"http://127.0.0.1:{server.server_address[1]}",
                str(Path(tmpdir) / "jira_cookie.json"),
            )
            state = JiraRuntimeAuthState(settings)
            client = JiraClient(settings, state)

            result = client.download_attachment(
                attachment_id="20001",
                issue_key=None,
                filename=None,
                output_dir=tmpdir,
                overwrite=False,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(Path(result["saved_path"]).read_text(), "select 1;\n")
