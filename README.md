# jira-mcp

MCP server (Python) for Jira read/write operations used in Telegram bot e2e testing.

## Features

- Search/read issues (`jira_search_issues`, `jira_get_issue`)
- Check available transitions (`jira_list_transitions`)
- Add worklog (`jira_add_worklog`)
- Update issue fields (`jira_update_issue`)
- Transition issue (`jira_transition_issue`)
- Add comment (`jira_add_comment`)
- Auth check (`jira_auth_status`)

Write safety:
- `confirm=true` is required for all write tools.
- Write is allowed only for issue/project whitelist from env.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Required env:
- `JIRA_BASE_URL`

Recommended for MVP:
- `JIRA_AUTH_MODE=cookie`
- `JIRA_COOKIE=...`
- `JIRA_WRITE_PROJECT_WHITELIST=TEAM`
- `JIRA_WRITE_ISSUE_WHITELIST=TEAM-123`

Auth fallback options:
- Basic: `JIRA_AUTH_MODE=basic` + `JIRA_USERNAME` + `JIRA_PASSWORD`
- Bearer: `JIRA_AUTH_MODE=bearer` + `JIRA_TOKEN`
- Auto: `JIRA_AUTH_MODE=auto` (cookie -> basic -> bearer by available env)

## Run

```bash
python -m jira_mcp.server
```

Server runs over stdio.

## Tool Examples

- `jira_auth_status()`
- `jira_search_issues(jql="project = TEAM ORDER BY updated DESC", limit=20)`
- `jira_get_issue(issue_key="TEAM-123")`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", confirm=True)`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", started="2026-03-04T09:30:00.000+0300", confirm=True)`
- `jira_transition_issue(issue_key="TEAM-123", transition_id="31", confirm=True)`

`started` format for worklog: `YYYY-MM-DDTHH:MM:SS.000+ZZZZ` (for example `+0300`).
