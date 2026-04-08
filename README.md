# jira-mcp

MCP server (Python) for Jira read/write operations used in Telegram bot e2e testing.

## Features

- Search/read issues (`jira_search_issues`, `jira_get_issue`)
- List board sprints (`jira_list_board_sprints`)
- Check available transitions (`jira_list_transitions`)
- Add worklog (`jira_add_worklog`)
- Create issue (`jira_create_issue`)
- Update issue fields and description (`jira_update_issue`)
- Transition issue (`jira_transition_issue`)
- Add comment (`jira_add_comment`)
- Update comment (`jira_update_comment`)
- Add attachment (`jira_add_attachment`)
- Add issues to sprint (`jira_add_issues_to_sprint`)
- Remove issues from sprint (`jira_remove_issues_from_sprint`)
- Auth check (`jira_auth_status`)

Write safety:
- `confirm=true` is required for all write tools.
- Write is allowed only for issue/project whitelist from env.
- Issue creation has a separate feature flag and is restricted to one configured project.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Required env:
- `JIRA_BASE_URL`

Recommended auth setup:
- `JIRA_AUTH_MODE=auto`
- `JIRA_COOKIE=...`
- `JIRA_USERNAME=...`
- `JIRA_PASSWORD=...`
- `JIRA_WRITE_PROJECT_WHITELIST=TEAM`
- `JIRA_WRITE_ISSUE_WHITELIST=TEAM-123`
- `JIRA_WRITE_SPRINT_WHITELIST=456,457`
- `JIRA_ENABLE_CREATE_ISSUE=false`
- `JIRA_CREATE_ISSUE_PROJECT_WHITELIST=TEAM`

Auth modes:
- `cookie` - sends raw `JIRA_COOKIE` header.
- `basic` - sends `Authorization: Basic ...`.
- `basic_with_cookies` - same as `basic`, but reuses Jira session cookies across requests.
- `bearer` - sends `Authorization: Bearer <JIRA_BEARER_TOKEN>` with legacy fallback to `JIRA_TOKEN` or `JIRA_COOKIE`.
- `auto` - tries `cookie -> basic_with_cookies -> basic -> bearer` by available env.

Browser recovery env:
- `JIRA_ENABLE_BROWSER_RECOVERY=false`
- `JIRA_BROWSER_RECOVERY_SCRIPT_PATH=scripts/jira_browser_recover.py`
- `JIRA_BROWSER_PROFILE_DIR=jira_browser_profile`
- `JIRA_INTERNAL_COOKIE_STORAGE_PATH=.state/jira_cookie.json`
- `JIRA_BROWSER_RECOVERY_COOLDOWN_MINUTES=60`

Create issue env:
- `JIRA_ENABLE_CREATE_ISSUE=false`
- `JIRA_CREATE_ISSUE_PROJECT_WHITELIST=TEAM`

Sprint write env:
- `JIRA_WRITE_SPRINT_WHITELIST=456,457`

Sprint write notes:
- `jira_add_issues_to_sprint` and `jira_remove_issues_from_sprint` work only with `confirm=true`.
- Both tools require issue write permission from `JIRA_WRITE_PROJECT_WHITELIST` or `JIRA_WRITE_ISSUE_WHITELIST`.
- Both tools also require `sprint_id` to be included in `JIRA_WRITE_SPRINT_WHITELIST`.
- `jira_remove_issues_from_sprint` moves issues to backlog via Jira Agile API.

Create issue notes:
- `jira_create_issue` works only with `confirm=true`.
- Creation is blocked unless `JIRA_ENABLE_CREATE_ISSUE=true`.
- Creation is allowed only when `project_key` is included in `JIRA_CREATE_ISSUE_PROJECT_WHITELIST`.
- MVP payload supports `project_key`, `summary`, `issue_type` (name or id), optional `description`, and extra raw `fields` for Jira-specific required fields.

Browser recovery notes:
- Recovery runs only after normal auth fallback is exhausted.
- Recovered cookie is persisted in `.state/jira_cookie.json` by default and becomes the preferred runtime source.
- If Playwright is not installed, normal auth still works and recovery returns a clear error.
- To clear stale recovered cookie manually, remove `.state/jira_cookie.json` and restart the server.

## Run

```bash
python -m jira_mcp.server
```

Server runs over stdio.

## Tests

```bash
python -m unittest discover -s tests
```

## Tool Examples

- `jira_auth_status()`
- `jira_search_issues(jql="project = TEAM ORDER BY updated DESC", limit=20)`
- `jira_get_issue(issue_key="TEAM-123")`
- `jira_list_board_sprints(board_id=865, state="active")`
- `jira_get_current_board_sprint(board_id=865)`
- `jira_create_issue(project_key="TEAM", summary="Prepare release notes", confirm=True)`
- `jira_create_issue(project_key="TEAM", summary="Prepare release notes", issue_type="Bug", description="Reported by QA", fields={"priority": {"name": "High"}}, confirm=True)`
- `jira_create_issue(project_key="AQ", summary="Prepare AQ task", issue_type="10006", fields={"assignee": {"name": "<login>"}, "components": [{"id": "18340"}], "customfield_10901": {"id": "10403"}}, confirm=True)`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", confirm=True)`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", started="2026-03-04T09:30:00.000+0300", confirm=True)`
- `jira_update_issue(issue_key="TEAM-123", description="New description", confirm=True)`
- `jira_update_issue(issue_key="TEAM-123", description="New description", fields={"priority": {"name": "High"}}, confirm=True)`
- `jira_update_description(issue_key="TEAM-123", description="New description", confirm=True)`
- `jira_transition_issue(issue_key="TEAM-123", transition_id="31", confirm=True)`
- `jira_update_comment(issue_key="TEAM-123", comment_id="456", comment="Updated text", confirm=True)`
- `jira_add_attachment(issue_key="TEAM-123", file_path="/tmp/report.txt", confirm=True)`
- `jira_add_issues_to_sprint(sprint_id=456, issue_keys=["AQ-123", "AQ-124"], confirm=True)`
- `jira_remove_issues_from_sprint(sprint_id=456, issue_keys=["AQ-123"], confirm=True)`
- `jira_add_issues_to_current_board_sprint(board_id=865, issue_keys=["AQ-123"], confirm=True)`
- `jira_remove_issues_from_current_board_sprint(board_id=865, issue_keys=["AQ-123"], confirm=True)`

`started` format for worklog: `YYYY-MM-DDTHH:MM:SS.000+ZZZZ` (for example `+0300`).
