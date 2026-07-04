# jira-mcp

MCP server (Python) for Jira read/write operations used in Telegram bot e2e testing.

## Features

- Search/read issues (`jira_search_issues`, `jira_get_issue`)
- Text search over the local Jira cache (`jira_search_cached_issues`)
- List board sprints (`jira_list_board_sprints`)
- Create/update/start/close sprints (`jira_create_sprint`, `jira_update_sprint`, `jira_start_sprint`, `jira_close_sprint`)
- Check available transitions (`jira_list_transitions`)
- Add worklog (`jira_add_worklog`)
- Create issue (`jira_create_issue`)
- Update issue fields and description (`jira_update_issue`)
- Link issues (`jira_link_issues`)
- Transition issue (`jira_transition_issue`)
- Add comment (`jira_add_comment`)
- Update comment (`jira_update_comment`)
- Delete comment (`jira_delete_comment`)
- Add attachment (`jira_add_attachment`)
- Download attachment (`jira_download_attachment`)
- Delete issue link (`jira_delete_issue_link`)
- Add issues to sprint (`jira_add_issues_to_sprint`)
- Remove issues from sprint (`jira_remove_issues_from_sprint`)
- Auth check (`jira_auth_status`)

Write safety:
- `confirm=true` is required for all write tools.
- `jira_add_comment` additionally requires a separate explicit confirmation string: `comment_confirm="ADD_COMMENT <ISSUE-KEY>"`.
- `jira_delete_comment` additionally requires a separate explicit confirmation string: `comment_confirm="DELETE_COMMENT <ISSUE-KEY> <COMMENT-ID>"`.
- Write is allowed only for issue/project whitelist from env.
- Sprint management is allowed only for sprint or board whitelist from env.
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
- `JIRA_WRITE_BOARD_WHITELIST=865`
- `JIRA_ENABLE_CREATE_ISSUE=false`
- `JIRA_CREATE_ISSUE_PROJECT_WHITELIST=TEAM`
- `JIRA_ENABLE_CACHE=false`
- `JIRA_CACHE_PATH=.state/jira_cache.json`
- `JIRA_CACHE_TTL_SECONDS=3600`
- `JIRA_CACHE_MAX_ENTRIES=1000`

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

Cache env:
- `JIRA_ENABLE_CACHE=false`
- `JIRA_CACHE_PATH=.state/jira_cache.json`
- `JIRA_CACHE_TTL_SECONDS=3600`
- `JIRA_CACHE_MAX_ENTRIES=1000`

Create issue env:
- `JIRA_ENABLE_CREATE_ISSUE=false`
- `JIRA_CREATE_ISSUE_PROJECT_WHITELIST=TEAM`

Sprint write env:
- `JIRA_WRITE_SPRINT_WHITELIST=456,457`
- `JIRA_WRITE_BOARD_WHITELIST=865`

Sprint write notes:
- `jira_add_issues_to_sprint` and `jira_remove_issues_from_sprint` work only with `confirm=true`.
- Both tools require issue write permission from `JIRA_WRITE_PROJECT_WHITELIST` or `JIRA_WRITE_ISSUE_WHITELIST`.
- Both tools also require either `sprint_id` in `JIRA_WRITE_SPRINT_WHITELIST` or the sprint's `originBoardId` in `JIRA_WRITE_BOARD_WHITELIST`.
- `jira_remove_issues_from_sprint` moves issues to backlog via Jira Agile API.
- `jira_create_sprint` requires `board_id` to be included in `JIRA_WRITE_BOARD_WHITELIST`.
- `jira_update_sprint`, `jira_start_sprint`, and `jira_close_sprint` require either `sprint_id` in `JIRA_WRITE_SPRINT_WHITELIST` or the sprint's `originBoardId` in `JIRA_WRITE_BOARD_WHITELIST`.

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

Cache notes:
- Status: implemented behind `JIRA_ENABLE_CACHE=true`.
- Cache is opt-in and affects only read tools.
- `jira_get_issue` and `jira_search_issues` use read-through cache when `JIRA_ENABLE_CACHE=true`.
- Stale cached issues with a stored `fields.updated` value are revalidated with a lightweight `fields=updated` request before downloading the full issue.
- Write tools invalidate affected issue entries and clear cached search results after successful Jira writes.
- `jira_search_cached_issues` searches only locally cached issue payloads and never calls Jira.

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
- `jira_search_cached_issues(query="release notes", limit=20)`
- `jira_get_issue(issue_key="TEAM-123")`
- `jira_list_board_sprints(board_id=865, state="active")`
- `jira_get_current_board_sprint(board_id=865)`
- `jira_create_sprint(board_id=865, name="SCRUM Спринт 68", start_date="2026-06-01T09:00:00.000+03:00", end_date="2026-06-12T21:00:00.000+03:00", goal="ЦР для физлиц, срочные задачи ИЭ, рекурренты СБП", confirm=True)`
- `jira_start_sprint(sprint_id=456, start_date="2026-06-01T09:00:00.000+03:00", end_date="2026-06-12T21:00:00.000+03:00", confirm=True)`
- `jira_close_sprint(sprint_id=312, confirm=True)`
- `jira_create_issue(project_key="TEAM", summary="Prepare release notes", confirm=True)`
- `jira_create_issue(project_key="TEAM", summary="Prepare release notes", issue_type="Bug", description="Reported by QA", fields={"priority": {"name": "High"}}, confirm=True)`
- `jira_create_issue(project_key="AQ", summary="Prepare AQ task", issue_type="10006", fields={"assignee": {"name": "<login>"}, "components": [{"id": "18340"}], "customfield_10901": {"id": "10403"}}, confirm=True)`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", confirm=True)`
- `jira_add_worklog(issue_key="TEAM-123", minutes=30, comment="e2e", started="2026-03-04T09:30:00.000+0300", confirm=True)`
- `jira_update_issue(issue_key="TEAM-123", description="New description", confirm=True)`
- `jira_update_issue(issue_key="TEAM-123", description="New description", fields={"priority": {"name": "High"}}, confirm=True)`
- `jira_link_issues(source_issue_key="PV-1", target_issue_key="FRMM-1", link_type="Relates", confirm=True)`
- `jira_update_description(issue_key="TEAM-123", description="New description", confirm=True)`
- `jira_transition_issue(issue_key="TEAM-123", transition_id="31", confirm=True)`
- `jira_add_comment(issue_key="TEAM-123", comment="Need DBA check", comment_confirm="ADD_COMMENT TEAM-123", confirm=True)`
- `jira_update_comment(issue_key="TEAM-123", comment_id="456", comment="Updated text", confirm=True)`
- `jira_delete_comment(issue_key="TEAM-123", comment_id="456", comment_confirm="DELETE_COMMENT TEAM-123 456", confirm=True)`
- `jira_add_attachment(issue_key="TEAM-123", file_path="/tmp/report.txt", confirm=True)`
- `jira_download_attachment(attachment_id="20001", output_dir="/tmp/opencode")`
- `jira_download_attachment(issue_key="TEAM-123", filename="report.txt", output_dir="/tmp/opencode")`
- `jira_delete_issue_link(link_id="12345", source_issue_key="TEAM-123", target_issue_key="TEAM-124", confirm=True)`
- `jira_add_issues_to_sprint(sprint_id=456, issue_keys=["AQ-123", "AQ-124"], confirm=True)`
- `jira_remove_issues_from_sprint(sprint_id=456, issue_keys=["AQ-123"], confirm=True)`
- `jira_add_issues_to_current_board_sprint(board_id=865, issue_keys=["AQ-123"], confirm=True)`
- `jira_remove_issues_from_current_board_sprint(board_id=865, issue_keys=["AQ-123"], confirm=True)`

`started` format for worklog: `YYYY-MM-DDTHH:MM:SS.000+ZZZZ` (for example `+0300`).
