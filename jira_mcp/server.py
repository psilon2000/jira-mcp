from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .auth_state import JiraRuntimeAuthState
from .config import load_settings
from .jira_client import JiraClient
from .recovery import BrowserRecoveryService


settings = load_settings()
auth_state = JiraRuntimeAuthState(settings)
recovery_service = BrowserRecoveryService(settings, auth_state)
client = JiraClient(settings, auth_state, recovery_service)
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


def _expected_comment_confirmation(issue_key: str) -> str:
    issue = (issue_key or "").strip().upper()
    return f"ADD_COMMENT {issue}"


def _expected_delete_comment_confirmation(issue_key: str, comment_id: str) -> str:
    issue = (issue_key or "").strip().upper()
    comment = str(comment_id or "").strip()
    return f"DELETE_COMMENT {issue} {comment}"


def _ensure_comment_write_allowed(issue_key: str, confirm: bool, comment_confirm: str) -> str:
    _ensure_write_allowed(issue_key, confirm)
    issue = issue_key.strip().upper()
    expected = _expected_comment_confirmation(issue)
    actual = (comment_confirm or "").strip().upper()
    if actual != expected:
        raise ValueError(
            "jira_add_comment requires separate explicit confirmation: "
            f"comment_confirm must equal '{expected}'"
        )
    return issue


def _ensure_delete_comment_allowed(issue_key: str, comment_id: str, confirm: bool, comment_confirm: str) -> tuple[str, str]:
    _ensure_write_allowed(issue_key, confirm)
    issue = issue_key.strip().upper()
    comment = str(comment_id).strip()
    if not comment:
        raise ValueError("comment_id is required")
    expected = _expected_delete_comment_confirmation(issue, comment)
    actual = (comment_confirm or "").strip().upper()
    if actual != expected:
        raise ValueError(
            "jira_delete_comment requires separate explicit confirmation: "
            f"comment_confirm must equal '{expected}'"
        )
    return issue, comment


def _ensure_sprint_write_allowed(sprint_id: int, confirm: bool) -> int:
    if not confirm:
        raise ValueError("write requires explicit confirm=true")

    sprint = _normalize_sprint_id(sprint_id)
    if sprint not in settings.write_sprint_whitelist:
        if not settings.write_sprint_whitelist and not settings.write_board_whitelist:
            raise ValueError("write sprint whitelist is empty: set JIRA_WRITE_SPRINT_WHITELIST or JIRA_WRITE_BOARD_WHITELIST")
        if settings.write_board_whitelist:
            sprint_payload = client.get_sprint(sprint_id=sprint).get("sprint") or {}
            board_id = int(sprint_payload.get("originBoardId") or 0)
            if board_id in settings.write_board_whitelist:
                return sprint
        raise ValueError(f"sprint '{sprint}' is not allowed by sprint or board whitelist")

    return sprint


def _normalize_sprint_id(sprint_id: int) -> int:
    sprint = int(sprint_id)
    if sprint <= 0:
        raise ValueError("sprint_id must be a positive integer")
    return sprint


def _ensure_board_write_allowed(board_id: int, confirm: bool) -> int:
    if not confirm:
        raise ValueError("write requires explicit confirm=true")

    board = _normalize_board_id(board_id)
    if not settings.write_board_whitelist:
        raise ValueError("write board whitelist is empty: set JIRA_WRITE_BOARD_WHITELIST")

    if board not in settings.write_board_whitelist:
        raise ValueError(f"board '{board}' is not allowed by board whitelist")

    return board


def _ensure_sprint_manage_allowed(sprint_id: int, confirm: bool) -> int:
    if not confirm:
        raise ValueError("write requires explicit confirm=true")

    sprint = _normalize_sprint_id(sprint_id)
    if sprint in settings.write_sprint_whitelist:
        return sprint

    if settings.write_board_whitelist:
        sprint_payload = client.get_sprint(sprint_id=sprint).get("sprint") or {}
        board_id = int(sprint_payload.get("originBoardId") or 0)
        if board_id in settings.write_board_whitelist:
            return sprint

    raise ValueError(f"sprint '{sprint}' is not allowed by sprint or board whitelist")


def _normalize_issue_keys(issue_keys: list[str]) -> list[str]:
    normalized: list[str] = []
    for issue_key in issue_keys:
        issue = (issue_key or "").strip().upper()
        if not issue:
            continue
        normalized.append(issue)
    if not normalized:
        raise ValueError("issue_keys must not be empty")
    return normalized


def _normalize_board_id(board_id: int) -> int:
    board = int(board_id)
    if board <= 0:
        raise ValueError("board_id must be a positive integer")
    return board


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_sprint_state(state: str | None) -> str | None:
    if state is None:
        return None
    value = state.strip().lower()
    if value not in {"future", "active", "closed"}:
        raise ValueError("state must be one of: future, active, closed")
    return value


def _resolve_current_board_sprint(board_id: int) -> dict[str, Any]:
    board = _normalize_board_id(board_id)
    result = client.get_current_board_sprint(board_id=board)
    sprint = result.get("sprint")
    if not sprint:
        raise ValueError(f"board '{board}' has no active or future sprint")
    sprint_id = sprint.get("id")
    if not sprint_id:
        raise ValueError(f"board '{board}' returned sprint without id")
    result["board_id"] = board
    result["sprint_id"] = int(sprint_id)
    return result


def _ensure_create_issue_allowed(project_key: str, confirm: bool) -> str:
    if not confirm:
        raise ValueError("create requires explicit confirm=true")
    if not settings.enable_create_issue:
        raise ValueError("create issue is disabled: set JIRA_ENABLE_CREATE_ISSUE=true")
    if not settings.create_issue_project_whitelist:
        raise ValueError(
            "create issue project whitelist is not configured: set JIRA_CREATE_ISSUE_PROJECT_WHITELIST"
        )

    project = (project_key or "").strip().upper()
    if not project:
        raise ValueError("project_key is required")
    if project not in settings.create_issue_project_whitelist:
        raise ValueError(f"project '{project}' is not allowed for issue creation")
    return project


@mcp.tool()
def jira_auth_status() -> dict[str, Any]:
    """Check Jira auth using /myself endpoint."""
    try:
        return client.auth_status()
    except Exception as exc:
        return {
            "status": "error",
            "authorized": False,
            "cookie_source": auth_state.get_active_source(),
            "error": str(exc),
        }


@mcp.tool()
def jira_search_users(query: str, max_results: int | None = None) -> dict[str, Any]:
    """Search Jira users by name, email, or username (confirm + whitelist not required)."""
    if not query.strip():
        raise ValueError("query is required")
    return client.search_users(query=query.strip(), max_results=max_results or settings.default_limit)


@mcp.tool()
def jira_search_issues(jql: str, fields: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
    """Search issues by JQL."""
    if not jql.strip():
        raise ValueError("jql is required")
    return client.search_issues(jql=jql, fields=fields, limit=limit or settings.default_limit)


@mcp.tool()
def jira_search_cached_issues(query: str, limit: int | None = None) -> dict[str, Any]:
    """Text search over locally cached Jira issue payloads only."""
    if not query.strip():
        raise ValueError("query is required")
    return client.search_cached_issues(query=query.strip(), limit=limit or settings.default_limit)


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
def jira_list_board_sprints(
    board_id: int,
    state: str | None = None,
    limit: int | None = None,
    start_at: int = 0,
) -> dict[str, Any]:
    """List board sprints by Agile board id."""
    board = _normalize_board_id(board_id)
    state_value = state.strip() if state else None
    return client.list_board_sprints(
        board_id=board,
        state=state_value,
        limit=limit or settings.default_limit,
        start_at=start_at,
    )


@mcp.tool()
def jira_get_current_board_sprint(board_id: int) -> dict[str, Any]:
    """Get current sprint for board id: active sprint or nearest future sprint."""
    return _resolve_current_board_sprint(board_id)


@mcp.tool()
def jira_create_sprint(
    board_id: int,
    name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    goal: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a Jira sprint under a board (confirm + board whitelist required)."""
    board = _ensure_board_write_allowed(board_id, confirm)
    name_value = name.strip()
    if not name_value:
        raise ValueError("name is required")
    return client.create_sprint(
        board_id=board,
        name=name_value,
        start_date=_optional_text(start_date, "start_date"),
        end_date=_optional_text(end_date, "end_date"),
        goal=_optional_text(goal, "goal"),
    )


@mcp.tool()
def jira_update_sprint(
    sprint_id: int,
    name: str | None = None,
    state: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    goal: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Update sprint fields (confirm + sprint or board whitelist required)."""
    if not confirm:
        raise ValueError("write requires explicit confirm=true")
    name_value = _optional_text(name, "name")
    state_value = _normalize_sprint_state(state)
    start_date_value = _optional_text(start_date, "start_date")
    end_date_value = _optional_text(end_date, "end_date")
    goal_value = _optional_text(goal, "goal")

    if all(value is None for value in (name_value, state_value, start_date_value, end_date_value, goal_value)):
        raise ValueError("at least one sprint field must be provided")

    sprint = _ensure_sprint_manage_allowed(sprint_id, confirm)
    return client.update_sprint(
        sprint_id=sprint,
        name=name_value,
        state=state_value,
        start_date=start_date_value,
        end_date=end_date_value,
        goal=goal_value,
    )


@mcp.tool()
def jira_start_sprint(
    sprint_id: int,
    start_date: str,
    end_date: str,
    name: str | None = None,
    goal: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Start a Jira sprint (confirm + sprint or board whitelist required)."""
    if not confirm:
        raise ValueError("write requires explicit confirm=true")
    start_date_value = _optional_text(start_date, "start_date")
    end_date_value = _optional_text(end_date, "end_date")
    if start_date_value is None or end_date_value is None:
        raise ValueError("start_date and end_date are required")

    sprint = _ensure_sprint_manage_allowed(sprint_id, confirm)
    return client.update_sprint(
        sprint_id=sprint,
        name=_optional_text(name, "name"),
        state="active",
        start_date=start_date_value,
        end_date=end_date_value,
        goal=_optional_text(goal, "goal"),
    )


@mcp.tool()
def jira_close_sprint(sprint_id: int, confirm: bool = False) -> dict[str, Any]:
    """Close a Jira sprint (confirm + sprint or board whitelist required)."""
    sprint = _ensure_sprint_manage_allowed(sprint_id, confirm)
    return client.update_sprint(sprint_id=sprint, state="closed")


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
def jira_update_issue(
    issue_key: str,
    description: str | None = None,
    fields: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Update issue fields, including description (confirm + whitelist required)."""
    _ensure_write_allowed(issue_key, confirm)
    issue = issue_key.strip().upper()

    payload_fields = dict(fields or {})
    if description is not None:
        payload_fields["description"] = description

    if not payload_fields:
        raise ValueError("description or fields must not be empty")

    return client.update_issue(issue_key=issue, fields=payload_fields)


@mcp.tool()
def jira_update_description(issue_key: str, description: str, confirm: bool = False) -> dict[str, Any]:
    """Update only the issue description (confirm + whitelist required)."""
    if not (description or "").strip():
        raise ValueError("description must not be empty")
    return jira_update_issue(issue_key=issue_key, description=description, confirm=confirm)


@mcp.tool()
def jira_create_issue(
    project_key: str,
    summary: str,
    issue_type: str = "Task",
    description: str | None = None,
    fields: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a Jira issue (confirm + feature flag + project restriction required)."""
    project = _ensure_create_issue_allowed(project_key, confirm)
    summary_value = summary.strip()
    if not summary_value:
        raise ValueError("summary is required")

    issue_type_value = issue_type.strip()
    if not issue_type_value:
        raise ValueError("issue_type is required")

    return client.create_issue(
        project_key=project,
        summary=summary_value,
        issue_type=issue_type_value,
        description=description,
        fields=fields,
    )


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
def jira_add_comment(
    issue_key: str,
    comment: str,
    comment_confirm: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Add comment to issue (confirm + whitelist + separate comment confirmation required)."""
    issue = _ensure_comment_write_allowed(issue_key, confirm, comment_confirm)
    if not comment.strip():
        raise ValueError("comment must not be empty")
    return client.add_comment(issue_key=issue, comment=comment)


@mcp.tool()
def jira_link_issues(
    source_issue_key: str,
    target_issue_key: str,
    link_type: str = "Relates",
    comment: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create issue link between two issues (confirm + whitelist required for both)."""
    _ensure_write_allowed(source_issue_key, confirm)
    _ensure_write_allowed(target_issue_key, confirm)
    source_issue = source_issue_key.strip().upper()
    target_issue = target_issue_key.strip().upper()
    if not source_issue:
        raise ValueError("source_issue_key is required")
    if not target_issue:
        raise ValueError("target_issue_key is required")
    link_type_value = (link_type or "").strip()
    if not link_type_value:
        raise ValueError("link_type is required")
    return client.link_issues(
        source_issue_key=source_issue,
        target_issue_key=target_issue,
        link_type=link_type_value,
        comment=comment.strip() if comment and comment.strip() else None,
    )


@mcp.tool()
def jira_delete_issue_link(
    link_id: str,
    source_issue_key: str,
    target_issue_key: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete issue link by id (confirm + whitelist required for both issues)."""
    _ensure_write_allowed(source_issue_key, confirm)
    _ensure_write_allowed(target_issue_key, confirm)
    link_id_value = str(link_id).strip()
    source_issue = source_issue_key.strip().upper()
    target_issue = target_issue_key.strip().upper()
    if not link_id_value:
        raise ValueError("link_id is required")
    if not source_issue:
        raise ValueError("source_issue_key is required")
    if not target_issue:
        raise ValueError("target_issue_key is required")
    return client.delete_issue_link(
        link_id=link_id_value,
        source_issue_key=source_issue,
        target_issue_key=target_issue,
    )


@mcp.tool()
def jira_update_comment(issue_key: str, comment_id: str, comment: str, confirm: bool = False) -> dict[str, Any]:
    """Update comment on issue (confirm + whitelist required)."""
    _ensure_write_allowed(issue_key, confirm)
    issue = issue_key.strip().upper()
    comment_id_value = str(comment_id).strip()
    if not comment_id_value:
        raise ValueError("comment_id is required")
    if not comment.strip():
        raise ValueError("comment must not be empty")
    return client.update_comment(issue_key=issue, comment_id=comment_id_value, comment=comment)


@mcp.tool()
def jira_delete_comment(
    issue_key: str,
    comment_id: str,
    comment_confirm: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete comment from issue (confirm + whitelist + separate comment confirmation required)."""
    issue, comment = _ensure_delete_comment_allowed(issue_key, comment_id, confirm, comment_confirm)
    return client.delete_comment(issue_key=issue, comment_id=comment)


@mcp.tool()
def jira_add_attachment(issue_key: str, file_path: str, confirm: bool = False) -> dict[str, Any]:
    """Add attachment to issue (confirm + whitelist required)."""
    _ensure_write_allowed(issue_key, confirm)
    issue = issue_key.strip().upper()
    file_path_value = file_path.strip()
    if not file_path_value:
        raise ValueError("file_path is required")
    path = Path(file_path_value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"file_path does not exist or is not a file: {path}")
    return client.add_attachment(issue_key=issue, file_path=str(path))


@mcp.tool()
def jira_download_attachment(
    attachment_id: str | None = None,
    issue_key: str | None = None,
    filename: str | None = None,
    output_dir: str = "/tmp/opencode",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Download Jira attachment by attachment id or issue+filename."""
    attachment_id_value = (attachment_id or "").strip() or None
    issue_key_value = (issue_key or "").strip().upper() or None
    filename_value = (filename or "").strip() or None
    output_dir_value = (output_dir or "").strip()

    if not attachment_id_value and not (issue_key_value and filename_value):
        raise ValueError("attachment_id or issue_key+filename is required")
    if not output_dir_value:
        raise ValueError("output_dir is required")

    return client.download_attachment(
        attachment_id=attachment_id_value,
        issue_key=issue_key_value,
        filename=filename_value,
        output_dir=output_dir_value,
        overwrite=overwrite,
    )


@mcp.tool()
def jira_add_issues_to_sprint(sprint_id: int, issue_keys: list[str], confirm: bool = False) -> dict[str, Any]:
    """Add issues to sprint (confirm + issue whitelist + sprint whitelist required)."""
    sprint = _ensure_sprint_write_allowed(sprint_id, confirm)
    issues = _normalize_issue_keys(issue_keys)
    for issue_key in issues:
        _ensure_write_allowed(issue_key, confirm)
    return client.add_issues_to_sprint(sprint_id=sprint, issue_keys=issues)


@mcp.tool()
def jira_remove_issues_from_sprint(sprint_id: int, issue_keys: list[str], confirm: bool = False) -> dict[str, Any]:
    """Remove issues from sprint into backlog (confirm + issue whitelist + sprint whitelist required)."""
    _ensure_sprint_write_allowed(sprint_id, confirm)
    issues = _normalize_issue_keys(issue_keys)
    for issue_key in issues:
        _ensure_write_allowed(issue_key, confirm)
    result = client.remove_issues_from_sprint(issue_keys=issues)
    result["sprint_id"] = int(sprint_id)
    return result


@mcp.tool()
def jira_add_issues_to_current_board_sprint(
    board_id: int,
    issue_keys: list[str],
    confirm: bool = False,
) -> dict[str, Any]:
    """Add issues to board current sprint: active sprint or nearest future sprint."""
    current = _resolve_current_board_sprint(board_id)
    sprint = _ensure_sprint_write_allowed(current["sprint_id"], confirm)
    issues = _normalize_issue_keys(issue_keys)
    for issue_key in issues:
        _ensure_write_allowed(issue_key, confirm)
    result = client.add_issues_to_sprint(sprint_id=sprint, issue_keys=issues)
    result["board_id"] = current["board_id"]
    result["selection"] = current["selection"]
    result["sprint"] = current["sprint"]
    return result


@mcp.tool()
def jira_remove_issues_from_current_board_sprint(
    board_id: int,
    issue_keys: list[str],
    confirm: bool = False,
) -> dict[str, Any]:
    """Remove issues from board current sprint to backlog."""
    current = _resolve_current_board_sprint(board_id)
    _ensure_sprint_write_allowed(current["sprint_id"], confirm)
    issues = _normalize_issue_keys(issue_keys)
    for issue_key in issues:
        _ensure_write_allowed(issue_key, confirm)
    result = client.remove_issues_from_sprint(issue_keys=issues)
    result["board_id"] = current["board_id"]
    result["selection"] = current["selection"]
    result["sprint_id"] = current["sprint_id"]
    result["sprint"] = current["sprint"]
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
