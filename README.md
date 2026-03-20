# jira-mcp

MCP server (Python) for Jira read/write operations used in Telegram bot e2e testing.

## Features

- Search/read issues (`jira_search_issues`, `jira_get_issue`)
- Check available transitions (`jira_list_transitions`)
- Add worklog (`jira_add_worklog`)
- Create issue (`jira_create_issue`)
- Update issue fields (`jira_update_issue`)
- Transition issue (`jira_transition_issue`)
- Add comment (`jira_add_comment`)
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
- `jira_create_issue(project_key="TEAM", summary="Prepare release notes", confirm=True)`
- `jira_create_issue(project_key="TEAM", summary="Prepare release notes", issue_type="Bug", description="Reported by QA", fields={"priority": {"name": "High"}}, confirm=True)`
- `jira_create_issue(project_key="AQ", summary="Prepare AQ task", issue_type="10006", fields={"assignee": {"name": "<login>"}, "components": [{"id": "18340"}], "customfield_10901": {"id": "10403"}}, confirm=True)`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", confirm=True)`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", started="2026-03-04T09:30:00.000+0300", confirm=True)`
- `jira_transition_issue(issue_key="TEAM-123", transition_id="31", confirm=True)`

`started` format for worklog: `YYYY-MM-DDTHH:MM:SS.000+ZZZZ` (for example `+0300`).
