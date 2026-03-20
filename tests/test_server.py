from __future__ import annotations

import importlib
import os
import unittest
from dataclasses import replace
from unittest.mock import patch


SERVER_ENV = {
    "JIRA_BASE_URL": "https://jira.example.local",
    "JIRA_AUTH_MODE": "cookie",
    "JIRA_COOKIE": "JSESSIONID=value",
}


class ServerCreateIssueTests(unittest.TestCase):
    def _load_server_module(self):
        config = importlib.import_module("jira_mcp.config")
        with patch.object(config, "load_dotenv", return_value=False), patch.dict(os.environ, SERVER_ENV, clear=True):
            module = importlib.import_module("jira_mcp.server")
            return importlib.reload(module)

    def test_create_issue_requires_confirm(self) -> None:
        server = self._load_server_module()
        with patch.object(
            server,
            "settings",
            replace(server.settings, enable_create_issue=True, create_issue_project_whitelist=("TEAM",)),
        ):
            with self.assertRaisesRegex(ValueError, "confirm=true"):
                server.jira_create_issue(project_key="TEAM", summary="Ship it")

    def test_create_issue_rejects_unapproved_project(self) -> None:
        server = self._load_server_module()
        with patch.object(
            server,
            "settings",
            replace(server.settings, enable_create_issue=True, create_issue_project_whitelist=("TEAM",)),
        ):
            with self.assertRaisesRegex(ValueError, "not allowed"):
                server.jira_create_issue(project_key="OPS", summary="Ship it", confirm=True)

    def test_create_issue_calls_client_for_allowed_project(self) -> None:
        server = self._load_server_module()
        with patch.object(
            server,
            "settings",
            replace(server.settings, enable_create_issue=True, create_issue_project_whitelist=("TEAM", "AQ")),
        ):
            with patch.object(server.client, "create_issue", return_value={"status": "ok", "issue_key": "TEAM-1"}) as create_issue:
                result = server.jira_create_issue(
                    project_key=" team ",
                    summary="Ship it",
                    issue_type="Task",
                    description="Create test issue",
                    fields={"priority": {"name": "High"}},
                    confirm=True,
                )

        self.assertEqual(result, {"status": "ok", "issue_key": "TEAM-1"})
        create_issue.assert_called_once_with(
            project_key="TEAM",
            summary="Ship it",
            issue_type="Task",
            description="Create test issue",
            fields={"priority": {"name": "High"}},
        )
