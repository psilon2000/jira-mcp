from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_settings
from .jira_client import JiraClient


settings = load_settings()
client = JiraClient(settings)
mcp = FastMCP("jira-mcp")


def _project_from_issue_key(issue_key: str) -> str:
    key = (issue_key or "").strip().upper()
    if "-" not in key:
        return ""
    return key.split("-", 1)[0]


def _ensure_write_allowed(issue_key: str, confirm: bool) -> None:
    if not confirm:
        raise ValueError("write requires explicit confirm=true")

    issue = issue_key.strip().upper()
    if not issue:
        raise ValueError("issue_key is required")

    if not settings.write_issue_whitelist and not settings.write_project_whitelist:
        raise ValueError(
            "write whitelist is empty: set JIRA_WRITE_PROJECT_WHITELIST and/or JIRA_WRITE_ISSUE_WHITELIST"
        )

    if issue in settings.write_issue_whitelist:
        return

    project = _project_from_issue_key(issue)
    if project and project in settings.write_project_whitelist:
        return

    raise ValueError(f"issue '{issue}' is not allowed by write whitelist")


@mcp.tool()
def jira_auth_status() -> dict[str, Any]:
    """Check Jira auth using /myself endpoint."""
    try:
        return client.auth_status()
    except Exception as exc:
        return {"status": "error", "authorized": False, "error": str(exc)}


@mcp.tool()
def jira_search_issues(jql: str, fields: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
    """Search issues by JQL."""
    if not jql.strip():
        raise ValueError("jql is required")
    return client.search_issues(jql=jql, fields=fields, limit=limit or settings.default_limit)


@mcp.tool()
def jira_get_issue(
    issue_key: str,
    fields: list[str] | None = None,
    expand: list[str] | None = None,
) -> dict[str, Any]:
    """Get issue details by key."""
    if not issue_key.strip():
        raise ValueError("issue_key is required")
    return client.get_issue(issue_key=issue_key.strip(), fields=fields, expand=expand)


@mcp.tool()
def jira_list_transitions(issue_key: str) -> dict[str, Any]:
    """List allowed transitions for issue."""
    if not issue_key.strip():
        raise ValueError("issue_key is required")
    return client.list_transitions(issue_key=issue_key.strip())


@mcp.tool()
def jira_add_worklog(
    issue_key: str,
    minutes: int,
    comment: str | None = None,
    started: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Add worklog in minutes to issue (confirm + whitelist required)."""
    _ensure_write_allowed(issue_key, confirm)
    return client.add_worklog(issue_key=issue_key.strip(), minutes=minutes, comment=comment, started=started)


@mcp.tool()
def jira_update_issue(issue_key: str, fields: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
    """Update issue fields (confirm + whitelist required)."""
    _ensure_write_allowed(issue_key, confirm)
    if not fields:
        raise ValueError("fields must not be empty")
    return client.update_issue(issue_key=issue_key.strip(), fields=fields)


@mcp.tool()
def jira_transition_issue(
    issue_key: str,
    transition_id: str,
    fields: dict[str, Any] | None = None,
    comment: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform workflow transition for issue (confirm + whitelist required)."""
    _ensure_write_allowed(issue_key, confirm)
    if not str(transition_id).strip():
        raise ValueError("transition_id is required")
    return client.transition_issue(
        issue_key=issue_key.strip(),
        transition_id=str(transition_id).strip(),
        fields=fields,
        comment=comment,
    )


@mcp.tool()
def jira_add_comment(issue_key: str, comment: str, confirm: bool = False) -> dict[str, Any]:
    """Add comment to issue (confirm + whitelist required)."""
    _ensure_write_allowed(issue_key, confirm)
    if not comment.strip():
        raise ValueError("comment must not be empty")
    return client.add_comment(issue_key=issue_key.strip(), comment=comment)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
