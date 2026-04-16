from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


SERVER_ENV = {
    "JIRA_BASE_URL": "https://jira.example.local",
    "JIRA_AUTH_MODE": "cookie",
    "JIRA_COOKIE": "JSESSIONID=value",
}


class ServerToolTests(unittest.TestCase):
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

    def test_update_issue_requires_description_or_fields(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "description or fields"):
                server.jira_update_issue(issue_key="AQ-1", confirm=True)

    def test_update_issue_passes_description_as_field(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with patch.object(server.client, "update_issue", return_value={"status": "ok"}) as update_issue:
                result = server.jira_update_issue(issue_key="aq-1", description="Updated description", confirm=True)

        self.assertEqual(result, {"status": "ok"})
        update_issue.assert_called_once_with(issue_key="AQ-1", fields={"description": "Updated description"})

    def test_update_issue_merges_description_with_fields(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with patch.object(server.client, "update_issue", return_value={"status": "ok"}) as update_issue:
                result = server.jira_update_issue(
                    issue_key="aq-1",
                    description="Updated description",
                    fields={"priority": {"name": "High"}},
                    confirm=True,
                )

        self.assertEqual(result, {"status": "ok"})
        update_issue.assert_called_once_with(
            issue_key="AQ-1",
            fields={"priority": {"name": "High"}, "description": "Updated description"},
        )

    def test_update_description_requires_non_empty_description(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "description must not be empty"):
                server.jira_update_description(issue_key="AQ-1", description="   ", confirm=True)

    def test_update_description_delegates_to_update_issue(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with patch.object(server, "jira_update_issue", return_value={"status": "ok"}) as update_issue:
                result = server.jira_update_description(issue_key="aq-1", description="Updated description", confirm=True)

        self.assertEqual(result, {"status": "ok"})
        update_issue.assert_called_once_with(issue_key="aq-1", description="Updated description", confirm=True)

    def test_update_comment_requires_comment_id(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "comment_id is required"):
                server.jira_update_comment(issue_key="AQ-1", comment_id="   ", comment="text", confirm=True)

    def test_add_comment_requires_confirm(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "confirm=true"):
                server.jira_add_comment(issue_key="AQ-1", comment="text", comment_confirm="ADD_COMMENT AQ-1")

    def test_add_comment_requires_separate_confirmation(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "comment_confirm must equal 'ADD_COMMENT AQ-1'"):
                server.jira_add_comment(issue_key="AQ-1", comment="text", confirm=True)

    def test_add_comment_calls_client(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with patch.object(server.client, "add_comment", return_value={"status": "ok", "comment_id": "123"}) as add_comment:
                result = server.jira_add_comment(
                    issue_key=" aq-1 ",
                    comment="Added",
                    comment_confirm="ADD_COMMENT AQ-1",
                    confirm=True,
                )

        self.assertEqual(result, {"status": "ok", "comment_id": "123"})
        add_comment.assert_called_once_with(issue_key="AQ-1", comment="Added")

    def test_link_issues_requires_confirm(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "confirm=true"):
                server.jira_link_issues(source_issue_key="AQ-1", target_issue_key="AQ-2")

    def test_link_issues_requires_non_empty_link_type(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "link_type is required"):
                server.jira_link_issues(
                    source_issue_key="AQ-1",
                    target_issue_key="AQ-2",
                    link_type="   ",
                    confirm=True,
                )

    def test_link_issues_calls_client(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ", "PV"))):
            with patch.object(server.client, "link_issues", return_value={"status": "ok"}) as link_issues:
                result = server.jira_link_issues(
                    source_issue_key=" aq-1 ",
                    target_issue_key=" pv-1 ",
                    link_type="Relates",
                    comment="Link frontend task",
                    confirm=True,
                )

        self.assertEqual(result, {"status": "ok"})
        link_issues.assert_called_once_with(
            source_issue_key="AQ-1",
            target_issue_key="PV-1",
            link_type="Relates",
            comment="Link frontend task",
        )

    def test_update_comment_requires_non_empty_comment(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "comment must not be empty"):
                server.jira_update_comment(issue_key="AQ-1", comment_id="123", comment="   ", confirm=True)

    def test_update_comment_calls_client(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with patch.object(server.client, "update_comment", return_value={"status": "ok", "comment_id": "123"}) as update_comment:
                result = server.jira_update_comment(issue_key="aq-1", comment_id=" 123 ", comment="Updated", confirm=True)

        self.assertEqual(result, {"status": "ok", "comment_id": "123"})
        update_comment.assert_called_once_with(issue_key="AQ-1", comment_id="123", comment="Updated")

    def test_add_issues_to_sprint_requires_confirm(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_sprint_whitelist=(123,), write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "confirm=true"):
                server.jira_add_issues_to_sprint(sprint_id=123, issue_keys=["AQ-1"])

    def test_add_attachment_requires_existing_file(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "does not exist"):
                server.jira_add_attachment(issue_key="AQ-1", file_path="/tmp/missing.sql", confirm=True)

    def test_add_attachment_calls_client(self) -> None:
        server = self._load_server_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "config.sql"
            file_path.write_text("select 1;\n")
            with patch.object(server, "settings", replace(server.settings, write_project_whitelist=("AQ",))):
                with patch.object(server.client, "add_attachment", return_value={"status": "ok", "attachment_id": "1"}) as add_attachment:
                    result = server.jira_add_attachment(issue_key="aq-1", file_path=f" {file_path} ", confirm=True)

        self.assertEqual(result, {"status": "ok", "attachment_id": "1"})
        add_attachment.assert_called_once_with(issue_key="AQ-1", file_path=str(file_path.resolve()))

    def test_add_issues_to_sprint_rejects_unapproved_sprint(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_sprint_whitelist=(123,), write_project_whitelist=("AQ",))):
            with self.assertRaisesRegex(ValueError, "not allowed"):
                server.jira_add_issues_to_sprint(sprint_id=999, issue_keys=["AQ-1"], confirm=True)

    def test_add_issues_to_sprint_calls_client(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_sprint_whitelist=(123,), write_project_whitelist=("AQ",))):
            with patch.object(server.client, "add_issues_to_sprint", return_value={"status": "ok"}) as add_issues:
                result = server.jira_add_issues_to_sprint(sprint_id=123, issue_keys=[" aq-1 ", "AQ-2"], confirm=True)

        self.assertEqual(result, {"status": "ok"})
        add_issues.assert_called_once_with(sprint_id=123, issue_keys=["AQ-1", "AQ-2"])

    def test_remove_issues_from_sprint_calls_client(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_sprint_whitelist=(321,), write_project_whitelist=("AQ",))):
            with patch.object(server.client, "remove_issues_from_sprint", return_value={"status": "ok", "issue_keys": ["AQ-1"]}) as remove_issues:
                result = server.jira_remove_issues_from_sprint(sprint_id=321, issue_keys=["aq-1"], confirm=True)

        self.assertEqual(result, {"status": "ok", "issue_keys": ["AQ-1"], "sprint_id": 321})
        remove_issues.assert_called_once_with(issue_keys=["AQ-1"])

    def test_list_board_sprints_validates_board_id(self) -> None:
        server = self._load_server_module()
        with self.assertRaisesRegex(ValueError, "positive integer"):
            server.jira_list_board_sprints(board_id=0)

    def test_get_current_board_sprint_delegates_to_client(self) -> None:
        server = self._load_server_module()
        expected = {"status": "ok", "board_id": 865, "selection": "future", "sprint": {"id": 293}}
        with patch.object(server.client, "get_current_board_sprint", return_value=expected) as get_current:
            result = server.jira_get_current_board_sprint(board_id=865)

        self.assertEqual(result["sprint_id"], 293)
        get_current.assert_called_once_with(board_id=865)

    def test_add_issues_to_current_board_sprint_calls_client(self) -> None:
        server = self._load_server_module()
        with patch.object(server, "settings", replace(server.settings, write_sprint_whitelist=(293,), write_project_whitelist=("AQ",))):
            with patch.object(server.client, "get_current_board_sprint", return_value={"status": "ok", "board_id": 865, "selection": "future", "sprint": {"id": 293, "name": "SCRUM Спринт 63"}}):
                with patch.object(server.client, "add_issues_to_sprint", return_value={"status": "ok", "sprint_id": 293, "issue_keys": ["AQ-1"]}) as add_issues:
                    result = server.jira_add_issues_to_current_board_sprint(board_id=865, issue_keys=["aq-1"], confirm=True)

        self.assertEqual(result["board_id"], 865)
        self.assertEqual(result["selection"], "future")
        add_issues.assert_called_once_with(sprint_id=293, issue_keys=["AQ-1"])
