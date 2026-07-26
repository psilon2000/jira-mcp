from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth_state import JiraRuntimeAuthState
from .cache import CacheHit, JiraCache
from .config import (
    AUTH_MODE_AUTO,
    AUTH_MODE_BASIC,
    AUTH_MODE_BASIC_WITH_COOKIES,
    AUTH_MODE_BEARER,
    AUTH_MODE_COOKIE,
    Settings,
)
from .recovery import BrowserRecoveryService


@dataclass(frozen=True)
class AuthAttempt:
    name: str


class JiraClient:
    def __init__(
        self,
        settings: Settings,
        auth_state: JiraRuntimeAuthState,
        recovery_service: BrowserRecoveryService | None = None,
    ) -> None:
        self._settings = settings
        self._auth_state = auth_state
        self._recovery_service = recovery_service
        self._sessions: dict[str, Session] = {}
        self._cache = JiraCache(settings)

    def auth_status(self) -> dict[str, Any]:
        response = self._request("GET", "/myself")
        data = response.json()
        return {
            "status": "ok",
            "account": {
                "name": data.get("name"),
                "displayName": data.get("displayName"),
                "emailAddress": data.get("emailAddress"),
                "active": data.get("active"),
            },
            "auth": {
                "mode": self._settings.auth_mode,
                "cookie_source": self._auth_state.get_active_source(),
            },
        }

    def search_issues(
        self,
        jql: str,
        fields: list[str] | None,
        limit: int,
        start_at: int = 0,
    ) -> dict[str, Any]:
        max_results = max(1, min(limit, 200))
        page_start = max(0, start_at)
        cached = self._cache.get_search(jql=jql, fields=fields, limit=max_results, start_at=page_start)
        if cached is not None:
            result = dict(cached.payload)
            result["cache"] = self._cache_info(cached, is_hit=True)
            return result

        params: dict[str, Any] = {
            "jql": jql,
            "startAt": page_start,
            "maxResults": max_results,
        }
        if fields:
            params["fields"] = ",".join(fields)

        response = self._request("GET", "/search", params=params)
        payload = response.json()
        issues = payload.get("issues", []) or []
        total = int(payload.get("total") or 0)
        response_start = int(payload.get("startAt") if payload.get("startAt") is not None else page_start)
        response_limit = int(payload.get("maxResults") if payload.get("maxResults") is not None else max_results)
        result = {
            "status": "ok",
            "total": total,
            "count": len(issues),
            "start_at": response_start,
            "max_results": response_limit,
            "is_last": response_start + len(issues) >= total,
            "issues": issues,
        }
        self._cache.put_search(
            jql=jql,
            fields=fields,
            limit=max_results,
            result=result,
            start_at=page_start,
        )
        if self._cache.enabled:
            result["cache"] = self._cache_info(None, is_hit=False)
        return result

    def get_issue(self, issue_key: str, fields: list[str] | None, expand: list[str] | None) -> dict[str, Any]:
        cached = self._cache.get_issue(issue_key=issue_key, fields=fields, expand=expand)
        if cached is not None:
            return {"status": "ok", "issue": cached.payload, "cache": self._cache_info(cached, is_hit=True)}

        stale = self._cache.get_issue(issue_key=issue_key, fields=fields, expand=expand, allow_stale=True)
        if stale is not None and stale.updated and self._issue_updated_matches(issue_key, stale.updated):
            self._cache.touch_issue(stale.key)
            refreshed = self._cache.get_issue(issue_key=issue_key, fields=fields, expand=expand)
            hit = refreshed or CacheHit(key=stale.key, payload=stale.payload, age_seconds=0, updated=stale.updated)
            return {"status": "ok", "issue": stale.payload, "cache": self._cache_info(hit, is_hit=True, revalidated=True)}

        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = ",".join(expand)
        response = self._request("GET", f"/issue/{issue_key}", params=params or None)
        issue = response.json()
        self._cache.put_issue(issue_key=issue_key, fields=fields, expand=expand, issue=issue)
        result = {"status": "ok", "issue": issue}
        if self._cache.enabled:
            result["cache"] = self._cache_info(None, is_hit=False)
        return result

    def list_issue_worklogs(self, issue_key: str, limit: int, start_at: int = 0) -> dict[str, Any]:
        max_results = max(1, min(limit, 200))
        page_start = max(0, start_at)
        response = self._request(
            "GET",
            f"/issue/{issue_key}/worklog",
            params={"startAt": page_start, "maxResults": max_results},
        )
        payload = response.json()
        worklogs = payload.get("worklogs", []) or []
        total = int(payload.get("total") or 0)
        response_start = int(payload.get("startAt") if payload.get("startAt") is not None else page_start)
        response_limit = int(payload.get("maxResults") if payload.get("maxResults") is not None else max_results)
        return {
            "status": "ok",
            "issue_key": issue_key,
            "total": total,
            "count": len(worklogs),
            "start_at": response_start,
            "max_results": response_limit,
            "is_last": response_start + len(worklogs) >= total,
            "worklogs": worklogs,
        }

    def get_issue_changelog(self, issue_key: str, limit: int, start_at: int = 0) -> dict[str, Any]:
        max_results = max(1, min(limit, 200))
        page_start = max(0, start_at)
        response = self._request(
            "GET",
            f"/issue/{issue_key}/changelog",
            params={"startAt": page_start, "maxResults": max_results},
            accepted_statuses={404},
        )
        source = "endpoint"
        if response.status_code == 404:
            issue_response = self._request(
                "GET",
                f"/issue/{issue_key}",
                params={"fields": "*none", "expand": "changelog"},
            )
            issue_payload = issue_response.json()
            changelog = issue_payload.get("changelog") if isinstance(issue_payload, dict) else None
            payload = changelog if isinstance(changelog, dict) else {}
            source = "issue_expand"
        else:
            payload = response.json()
        histories = payload.get("values") or payload.get("histories") or []
        total = int(payload.get("total") or 0)
        default_start = 0 if source == "issue_expand" else page_start
        response_start = int(payload.get("startAt") if payload.get("startAt") is not None else default_start)
        response_limit = int(payload.get("maxResults") if payload.get("maxResults") is not None else max_results)
        if source == "issue_expand" and response_start != page_start:
            raise ValueError("this Jira version exposes only the first changelog page through issue expansion")
        is_last = payload.get("isLast")
        if is_last is None:
            is_last = response_start + len(histories) >= total
        return {
            "status": "ok",
            "issue_key": issue_key,
            "total": total,
            "count": len(histories),
            "start_at": response_start,
            "max_results": response_limit,
            "is_last": bool(is_last),
            "source": source,
            "histories": histories,
        }

    def list_fields(self, query: str | None = None, custom_only: bool = False) -> dict[str, Any]:
        response = self._request("GET", "/field")
        payload = response.json()
        fields = payload if isinstance(payload, list) else []
        needle = (query or "").strip().casefold()
        filtered: list[dict[str, Any]] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_id = str(field.get("id") or "")
            is_custom = bool(field.get("custom")) or field_id.startswith("customfield_")
            if custom_only and not is_custom:
                continue
            searchable = " ".join(
                [
                    field_id,
                    str(field.get("name") or ""),
                    *[str(item) for item in field.get("clauseNames", []) or []],
                ]
            ).casefold()
            if needle and needle not in searchable:
                continue
            filtered.append(field)
        return {
            "status": "ok",
            "query": query,
            "custom_only": custom_only,
            "count": len(filtered),
            "fields": filtered,
        }

    def get_issue_edit_metadata(
        self,
        issue_key: str,
        field_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        response = self._request("GET", f"/issue/{issue_key}/editmeta")
        payload = response.json()
        fields = payload.get("fields") if isinstance(payload, dict) else None
        metadata = fields if isinstance(fields, dict) else {}
        if field_ids is not None:
            requested = {str(field_id).strip() for field_id in field_ids if str(field_id).strip()}
            metadata = {field_id: value for field_id, value in metadata.items() if field_id in requested}
        return {
            "status": "ok",
            "issue_key": issue_key,
            "count": len(metadata),
            "fields": metadata,
        }

    def get_project(self, project_key: str, expand: list[str] | None = None) -> dict[str, Any]:
        params = {"expand": ",".join(expand)} if expand else None
        response = self._request("GET", f"/project/{project_key}", params=params)
        return {"status": "ok", "project_key": project_key, "project": response.json()}

    def search_cached_issues(self, query: str, limit: int) -> dict[str, Any]:
        return self._cache.search_text(query=query, limit=limit)

    def list_board_sprints(
        self,
        board_id: int,
        state: str | None,
        limit: int,
        start_at: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "startAt": max(0, start_at),
            "maxResults": max(1, min(limit, 200)),
        }
        if state:
            params["state"] = state

        response = self._request_agile("GET", f"/board/{board_id}/sprint", params=params)
        payload = response.json()
        values = payload.get("values", []) or []
        return {
            "status": "ok",
            "board_id": board_id,
            "count": len(values),
            "is_last": payload.get("isLast"),
            "max_results": payload.get("maxResults"),
            "start_at": payload.get("startAt"),
            "total": payload.get("total"),
            "sprints": values,
        }

    def list_boards(
        self,
        project_key_or_id: str | None,
        name: str | None,
        board_type: str | None,
        limit: int,
        start_at: int = 0,
    ) -> dict[str, Any]:
        max_results = max(1, min(limit, 200))
        page_start = max(0, start_at)
        params: dict[str, Any] = {
            "startAt": page_start,
            "maxResults": max_results,
        }
        if project_key_or_id:
            params["projectKeyOrId"] = project_key_or_id
        if name:
            params["name"] = name
        if board_type:
            params["type"] = board_type

        response = self._request_agile("GET", "/board", params=params)
        payload = response.json()
        boards = payload.get("values", []) or []
        total = int(payload.get("total") or 0)
        response_start = int(payload.get("startAt") if payload.get("startAt") is not None else page_start)
        response_limit = int(payload.get("maxResults") if payload.get("maxResults") is not None else max_results)
        is_last = payload.get("isLast")
        if is_last is None:
            is_last = response_start + len(boards) >= total
        return {
            "status": "ok",
            "total": total,
            "count": len(boards),
            "start_at": response_start,
            "max_results": response_limit,
            "is_last": bool(is_last),
            "boards": boards,
        }

    def get_board_configuration(self, board_id: int) -> dict[str, Any]:
        response = self._request_agile("GET", f"/board/{board_id}/configuration")
        return {"status": "ok", "board_id": board_id, "configuration": response.json()}

    def get_sprint(self, sprint_id: int) -> dict[str, Any]:
        response = self._request_agile("GET", f"/sprint/{sprint_id}")
        return {"status": "ok", "sprint_id": sprint_id, "sprint": response.json()}

    def create_sprint(
        self,
        board_id: int,
        name: str,
        start_date: str | None,
        end_date: str | None,
        goal: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "originBoardId": board_id,
        }
        if start_date:
            body["startDate"] = start_date
        if end_date:
            body["endDate"] = end_date
        if goal:
            body["goal"] = goal

        response = self._request_agile("POST", "/sprint", json=body)
        sprint = response.json()
        return {
            "status": "ok",
            "board_id": board_id,
            "sprint_id": sprint.get("id"),
            "sprint": sprint,
        }

    def update_sprint(
        self,
        sprint_id: int,
        name: str | None = None,
        state: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        goal: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_sprint(sprint_id=sprint_id)["sprint"]
        body = self._sprint_update_payload(current)
        if name is not None:
            body["name"] = name
        if state is not None:
            body["state"] = state
        if start_date is not None:
            body["startDate"] = start_date
        if end_date is not None:
            body["endDate"] = end_date
        if goal is not None:
            body["goal"] = goal

        response = self._request_agile("PUT", f"/sprint/{sprint_id}", json=body)
        return {"status": "ok", "sprint_id": sprint_id, "sprint": response.json()}

    def get_current_board_sprint(self, board_id: int) -> dict[str, Any]:
        active = self.list_board_sprints(board_id=board_id, state="active", limit=1, start_at=0)
        active_sprints = active.get("sprints", []) or []
        if active_sprints:
            sprint = active_sprints[0]
            return {
                "status": "ok",
                "board_id": board_id,
                "selection": "active",
                "sprint": sprint,
            }

        future = self.list_board_sprints(board_id=board_id, state="future", limit=50, start_at=0)
        future_sprints = future.get("sprints", []) or []
        if future_sprints:
            sprint = sorted(
                future_sprints,
                key=lambda item: (
                    item.get("startDate") or "9999-12-31T23:59:59.999+0000",
                    item.get("id") or 0,
                ),
            )[0]
            return {
                "status": "ok",
                "board_id": board_id,
                "selection": "future",
                "sprint": sprint,
            }

        return {
            "status": "ok",
            "board_id": board_id,
            "selection": "none",
            "sprint": None,
        }

    def add_worklog(self, issue_key: str, minutes: int, comment: str | None, started: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"timeSpentSeconds": max(1, minutes) * 60}
        if comment:
            body["comment"] = comment
        if started:
            body["started"] = started
        else:
            body["started"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        response = self._request("POST", f"/issue/{issue_key}/worklog", json=body)
        payload = response.json()
        self._invalidate_issue_cache(issue_key)
        return {
            "status": "ok",
            "issue_key": issue_key,
            "worklog_id": payload.get("id"),
            "time_spent_seconds": payload.get("timeSpentSeconds"),
        }

    def update_issue(self, issue_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        self._request("PUT", f"/issue/{issue_key}", json={"fields": fields})
        self._invalidate_issue_cache(issue_key)
        return {"status": "ok", "issue_key": issue_key, "updated_fields": sorted(fields.keys())}

    def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str,
        description: str | None,
        fields: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload_fields = dict(fields or {})
        issue_type_payload = {"id": issue_type} if issue_type.isdigit() else {"name": issue_type}
        payload_fields["project"] = {"key": project_key}
        payload_fields["issuetype"] = issue_type_payload
        payload_fields["summary"] = summary
        if description is not None:
            payload_fields["description"] = description

        response = self._request("POST", "/issue", json={"fields": payload_fields})
        payload = response.json()
        self._cache.clear_searches()
        return {
            "status": "ok",
            "issue_key": payload.get("key"),
            "issue_id": payload.get("id"),
            "issue_url": payload.get("self"),
        }

    def list_transitions(self, issue_key: str) -> dict[str, Any]:
        response = self._request("GET", f"/issue/{issue_key}/transitions")
        payload = response.json()
        return {
            "status": "ok",
            "issue_key": issue_key,
            "transitions": payload.get("transitions", []),
        }

    def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        fields: dict[str, Any] | None,
        comment: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "transition": {"id": str(transition_id)},
        }
        if fields:
            body["fields"] = fields
        if comment:
            body["update"] = {
                "comment": [{"add": {"body": comment}}],
            }
        self._request("POST", f"/issue/{issue_key}/transitions", json=body)
        self._invalidate_issue_cache(issue_key)
        return {"status": "ok", "issue_key": issue_key, "transition_id": str(transition_id)}

    def add_comment(self, issue_key: str, comment: str) -> dict[str, Any]:
        response = self._request("POST", f"/issue/{issue_key}/comment", json={"body": comment})
        payload = response.json()
        self._invalidate_issue_cache(issue_key)
        return {"status": "ok", "issue_key": issue_key, "comment_id": payload.get("id")}

    def link_issues(
        self,
        source_issue_key: str,
        target_issue_key: str,
        link_type: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": {"name": link_type},
            "outwardIssue": {"key": target_issue_key},
            "inwardIssue": {"key": source_issue_key},
        }
        if comment:
            body["comment"] = {"body": comment}
        self._request("POST", "/issueLink", json=body)
        self._invalidate_issue_cache(source_issue_key, target_issue_key)
        return {
            "status": "ok",
            "source_issue_key": source_issue_key,
            "target_issue_key": target_issue_key,
            "link_type": link_type,
        }

    def delete_issue_link(self, link_id: str, source_issue_key: str, target_issue_key: str) -> dict[str, Any]:
        response = self._request("GET", f"/issueLink/{link_id}")
        link = response.json()
        actual_issue_keys = {
            str((link.get(side) or {}).get("key") or "").strip().upper()
            for side in ("inwardIssue", "outwardIssue")
        }
        expected_issue_keys = {source_issue_key.upper(), target_issue_key.upper()}
        if actual_issue_keys != expected_issue_keys:
            raise ValueError(
                f"issue link '{link_id}' connects {sorted(actual_issue_keys)}, expected {sorted(expected_issue_keys)}"
            )
        self._request("DELETE", f"/issueLink/{link_id}")
        self._invalidate_issue_cache(source_issue_key, target_issue_key)
        return {
            "status": "ok",
            "link_id": str(link_id),
            "source_issue_key": source_issue_key,
            "target_issue_key": target_issue_key,
            "link": link,
        }

    def update_comment(self, issue_key: str, comment_id: str, comment: str) -> dict[str, Any]:
        self._request("PUT", f"/issue/{issue_key}/comment/{comment_id}", json={"body": comment})
        self._invalidate_issue_cache(issue_key)
        return {"status": "ok", "issue_key": issue_key, "comment_id": str(comment_id)}

    def delete_comment(self, issue_key: str, comment_id: str) -> dict[str, Any]:
        self._request("DELETE", f"/issue/{issue_key}/comment/{comment_id}")
        self._invalidate_issue_cache(issue_key)
        return {"status": "ok", "issue_key": issue_key, "comment_id": str(comment_id)}

    def add_attachment(self, issue_key: str, file_path: str) -> dict[str, Any]:
        path = Path(file_path).expanduser().resolve()
        content = path.read_bytes()
        response = self._request(
            "POST",
            f"/issue/{issue_key}/attachments",
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (path.name, content)},
        )
        payload = response.json()
        self._invalidate_issue_cache(issue_key)
        attachments = payload if isinstance(payload, list) else []
        attachment = attachments[0] if attachments else {}
        return {
            "status": "ok",
            "issue_key": issue_key,
            "file_path": str(path),
            "filename": path.name,
            "attachment_id": attachment.get("id"),
            "attachment": attachment,
        }

    def delete_attachment(self, issue_key: str, attachment_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/issue/{issue_key}", params={"fields": "attachment"})
        issue = response.json()
        actual_issue_key = str(issue.get("key") or "").strip().upper() if isinstance(issue, dict) else ""
        if actual_issue_key != issue_key.upper():
            raise ValueError(
                f"issue lookup resolved to '{actual_issue_key or '[missing]'}', expected '{issue_key.upper()}'"
            )
        fields = issue.get("fields") if isinstance(issue, dict) else None
        attachments = fields.get("attachment", []) if isinstance(fields, dict) else []
        attachment = next(
            (
                item
                for item in attachments
                if isinstance(item, dict) and str(item.get("id") or "").strip() == attachment_id
            ),
            None,
        )
        if attachment is None:
            raise ValueError(f"attachment '{attachment_id}' not found in issue '{issue_key}'")

        self._request(
            "DELETE",
            f"/attachment/{attachment_id}",
            allow_forbidden_auth_fallback=False,
        )
        self._invalidate_issue_cache(issue_key)
        return {
            "status": "ok",
            "issue_key": issue_key,
            "attachment_id": attachment_id,
            "filename": attachment.get("filename"),
            "attachment": attachment,
        }

    def download_attachment(
        self,
        attachment_id: str | None,
        issue_key: str | None,
        filename: str | None,
        output_dir: str,
        overwrite: bool,
    ) -> dict[str, Any]:
        attachment = self._resolve_attachment(attachment_id=attachment_id, issue_key=issue_key, filename=filename)
        content_url = str(attachment.get("content") or "").strip()
        if not content_url:
            raise ValueError("attachment content URL is missing")

        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        safe_name = Path(str(attachment.get("filename") or attachment.get("id") or "attachment")).name or "attachment"
        target_path = output_path / safe_name
        if target_path.exists() and not overwrite:
            raise ValueError(f"output file already exists: {target_path}")

        response = self._request_download_url(content_url, f"attachment/{attachment.get('id')}")
        target_path.write_bytes(response.content)
        return {
            "status": "ok",
            "attachment_id": str(attachment.get("id") or ""),
            "filename": safe_name,
            "saved_path": str(target_path),
            "size": len(response.content),
            "mime_type": response.headers.get("Content-Type") or attachment.get("mimeType"),
            "attachment": attachment,
        }

    def search_users(self, query: str, max_results: int = 20) -> dict[str, Any]:
        max_results = max(1, min(max_results, 200))
        response = self._request("GET", "/user/search", params={"username": query, "maxResults": max_results})
        users = response.json()
        return {
            "status": "ok",
            "count": len(users),
            "users": [
                {
                    "key": u.get("name"),
                    "account_id": u.get("key"),
                    "display_name": u.get("displayName"),
                    "email": u.get("emailAddress"),
                    "active": u.get("active", False),
                }
                for u in (users or [])
            ],
        }

    def add_issues_to_sprint(self, sprint_id: int, issue_keys: list[str]) -> dict[str, Any]:
        self._request_agile("POST", f"/sprint/{sprint_id}/issue", json={"issues": issue_keys})
        self._invalidate_issue_cache(*issue_keys)
        return {
            "status": "ok",
            "sprint_id": sprint_id,
            "issue_keys": issue_keys,
            "issue_count": len(issue_keys),
        }

    def remove_issues_from_sprint(self, issue_keys: list[str]) -> dict[str, Any]:
        self._request_agile("POST", "/backlog/issue", json={"issues": issue_keys})
        self._invalidate_issue_cache(*issue_keys)
        return {
            "status": "ok",
            "issue_keys": issue_keys,
            "issue_count": len(issue_keys),
        }

    @staticmethod
    def _sprint_update_payload(sprint: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in ("id", "self", "state", "name", "startDate", "endDate", "completeDate", "originBoardId", "goal"):
            value = sprint.get(key)
            if value is not None:
                payload[key] = value
        return payload

    def _issue_updated_matches(self, issue_key: str, cached_updated: str) -> bool:
        try:
            response = self._request("GET", f"/issue/{issue_key}", params={"fields": "updated"})
            payload = response.json()
        except Exception:
            return False
        fields = payload.get("fields") if isinstance(payload, dict) else None
        updated = fields.get("updated") if isinstance(fields, dict) else None
        return updated == cached_updated

    def _cache_info(self, cache_hit: CacheHit | None, is_hit: bool, revalidated: bool = False) -> dict[str, Any]:
        info: dict[str, Any] = {
            "enabled": self._cache.enabled,
            "hit": is_hit,
        }
        if is_hit and cache_hit is not None:
            info["age_seconds"] = cache_hit.age_seconds
        if revalidated:
            info["revalidated"] = True
        return info

    def _invalidate_issue_cache(self, *issue_keys: str) -> None:
        for issue_key in issue_keys:
            self._cache.invalidate_issue(issue_key)

    def _resolve_attachment(
        self,
        attachment_id: str | None,
        issue_key: str | None,
        filename: str | None,
    ) -> dict[str, Any]:
        attachment_id_value = (attachment_id or "").strip()
        if attachment_id_value:
            response = self._request("GET", f"/attachment/{attachment_id_value}")
            payload = response.json()
            if isinstance(payload, dict):
                payload.setdefault("id", attachment_id_value)
                return payload
            return {}

        issue_key_value = (issue_key or "").strip()
        filename_value = (filename or "").strip()
        if not issue_key_value or not filename_value:
            raise ValueError("attachment_id or issue_key+filename is required")

        issue = self.get_issue(issue_key=issue_key_value, fields=["attachment"], expand=None)["issue"]
        attachments = ((issue.get("fields") or {}).get("attachment") or []) if isinstance(issue, dict) else []
        matches = [item for item in attachments if str(item.get("filename") or "") == filename_value]
        if not matches:
            raise ValueError(f"attachment '{filename_value}' not found in issue '{issue_key_value}'")
        if len(matches) > 1:
            raise ValueError(f"attachment filename is not unique in issue '{issue_key_value}': {filename_value}")
        return matches[0]

    def _request(
        self,
        method: str,
        path: str,
        allow_browser_recovery: bool = True,
        accepted_statuses: set[int] | None = None,
        allow_forbidden_auth_fallback: bool = True,
        **kwargs: Any,
    ) -> Response:
        url = f"{self._settings.rest_root.rstrip('/')}/{path.lstrip('/')}"
        return self._request_absolute_url(
            method,
            url,
            path,
            allow_browser_recovery=allow_browser_recovery,
            accepted_statuses=accepted_statuses,
            allow_forbidden_auth_fallback=allow_forbidden_auth_fallback,
            **kwargs,
        )

    def _request_agile(self, method: str, path: str, allow_browser_recovery: bool = True, **kwargs: Any) -> Response:
        url = f"{self._settings.agile_rest_root.rstrip('/')}/{path.lstrip('/')}"
        return self._request_absolute_url(method, url, path, allow_browser_recovery=allow_browser_recovery, **kwargs)

    def _request_download_url(self, url: str, path: str, allow_browser_recovery: bool = True) -> Response:
        attempts = self._build_auth_attempts()
        if not attempts:
            raise RuntimeError("Jira auth has no usable credentials")

        tried: list[str] = []
        for attempt in attempts:
            tried.append(attempt.name)
            response = self._send_with_attempt(
                attempt,
                "GET",
                url,
                headers={"Accept": "*/*"},
                allow_redirects=False,
            )
            if self._is_auth_failure(response) or self._is_login_redirect(response):
                if attempt.name == AUTH_MODE_COOKIE and self._auth_state.mark_active_cookie_invalid():
                    retried = f"{attempt.name}(configured_env)"
                    tried.append(retried)
                    fallback_response = self._send_with_attempt(
                        attempt,
                        "GET",
                        url,
                        headers={"Accept": "*/*"},
                        allow_redirects=False,
                    )
                    if fallback_response.ok and not fallback_response.is_redirect:
                        return fallback_response
                    if not self._is_auth_failure(fallback_response) and not self._is_login_redirect(fallback_response):
                        self._raise_for_response("GET", path, fallback_response)
                continue
            if response.is_redirect:
                self._raise_for_response("GET", path, response)
            if response.ok:
                return response
            self._raise_for_response("GET", path, response)

        if allow_browser_recovery and self._recovery_service is not None:
            recovery = self._recovery_service.try_recover(f"Jira auth failed after attempts: {' -> '.join(tried)}")
            if recovery.is_success:
                self._reset_sessions()
                return self._request_download_url(url, path, allow_browser_recovery=False)
            raise RuntimeError(
                f"Jira auth failed after attempts: {' -> '.join(tried)}; recovery={recovery.details}"
            )

        raise RuntimeError(f"Jira auth failed after attempts: {' -> '.join(tried)}")

    def _request_absolute_url(
        self,
        method: str,
        url: str,
        path: str,
        allow_browser_recovery: bool = True,
        accepted_statuses: set[int] | None = None,
        allow_forbidden_auth_fallback: bool = True,
        **kwargs: Any,
    ) -> Response:
        attempts = self._build_auth_attempts()
        if not attempts:
            raise RuntimeError("Jira auth has no usable credentials")

        tried: list[str] = []
        for attempt in attempts:
            tried.append(attempt.name)
            response = self._send_with_attempt(attempt, method, url, **kwargs)
            if response.ok or response.status_code in (accepted_statuses or set()):
                return response

            can_retry_auth = response.status_code != 403 or allow_forbidden_auth_fallback
            if self._is_auth_failure(response) and can_retry_auth:
                if attempt.name == AUTH_MODE_COOKIE and self._auth_state.mark_active_cookie_invalid():
                    retried = f"{attempt.name}(configured_env)"
                    tried.append(retried)
                    fallback_response = self._send_with_attempt(attempt, method, url, **kwargs)
                    if fallback_response.ok or fallback_response.status_code in (accepted_statuses or set()):
                        return fallback_response
                    fallback_can_retry = (
                        fallback_response.status_code != 403 or allow_forbidden_auth_fallback
                    )
                    if not self._is_auth_failure(fallback_response) or not fallback_can_retry:
                        self._raise_for_response(method, path, fallback_response)
                continue

            self._raise_for_response(method, path, response)

        if allow_browser_recovery and self._recovery_service is not None:
            recovery = self._recovery_service.try_recover(f"Jira auth failed after attempts: {' -> '.join(tried)}")
            if recovery.is_success:
                self._reset_sessions()
                return self._request_absolute_url(
                    method,
                    url,
                    path,
                    allow_browser_recovery=False,
                    accepted_statuses=accepted_statuses,
                    allow_forbidden_auth_fallback=allow_forbidden_auth_fallback,
                    **kwargs,
                )
            raise RuntimeError(
                f"Jira auth failed after attempts: {' -> '.join(tried)}; recovery={recovery.details}"
            )

        raise RuntimeError(f"Jira auth failed after attempts: {' -> '.join(tried)}")

    def _build_auth_attempts(self) -> list[AuthAttempt]:
        auth_mode = self._settings.auth_mode
        if auth_mode != AUTH_MODE_AUTO:
            return [AuthAttempt(auth_mode)] if self._has_credentials_for(auth_mode, auto_mode=False) else []

        attempts: list[AuthAttempt] = []
        for mode in (AUTH_MODE_COOKIE, AUTH_MODE_BASIC_WITH_COOKIES, AUTH_MODE_BASIC, AUTH_MODE_BEARER):
            if self._has_credentials_for(mode, auto_mode=True):
                attempts.append(AuthAttempt(mode))
        return attempts

    def _has_credentials_for(self, auth_mode: str, auto_mode: bool) -> bool:
        if auth_mode == AUTH_MODE_COOKIE:
            return bool(self._auth_state.get_cookie())
        if auth_mode in {AUTH_MODE_BASIC, AUTH_MODE_BASIC_WITH_COOKIES}:
            return bool(self._settings.username and self._settings.password)
        if auth_mode == AUTH_MODE_BEARER:
            return bool(self._resolve_bearer_token(allow_legacy_cookie_fallback=not auto_mode))
        return False

    def _resolve_bearer_token(self, allow_legacy_cookie_fallback: bool) -> str | None:
        if self._settings.token:
            return self._settings.token
        if allow_legacy_cookie_fallback:
            return self._auth_state.get_cookie()
        return None

    def _send_with_attempt(self, attempt: AuthAttempt, method: str, url: str, **kwargs: Any) -> Response:
        session = self._get_or_create_session(attempt.name)
        if attempt.name == AUTH_MODE_COOKIE:
            cookie = self._auth_state.get_cookie()
            session.headers.pop("Cookie", None)
            if cookie:
                session.headers["Cookie"] = cookie
        elif attempt.name == AUTH_MODE_BEARER:
            session.headers.pop("Authorization", None)
            token = self._resolve_bearer_token(allow_legacy_cookie_fallback=True)
            if token:
                session.headers["Authorization"] = f"Bearer {token}"

        return session.request(method, url, timeout=self._settings.timeout_sec, **kwargs)

    def _get_or_create_session(self, attempt_name: str) -> Session:
        session = self._sessions.get(attempt_name)
        if session is not None:
            return session

        session = requests.Session()
        session.headers.update({"Accept": "application/json"})
        retry = Retry(
            total=4,
            read=4,
            connect=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST", "PUT", "DELETE"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        if attempt_name in {AUTH_MODE_BASIC, AUTH_MODE_BASIC_WITH_COOKIES}:
            session.auth = (self._settings.username or "", self._settings.password or "")
        elif attempt_name == AUTH_MODE_COOKIE:
            session.cookies.clear()
        self._sessions[attempt_name] = session
        return session

    def _reset_sessions(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()

    @staticmethod
    def _is_auth_failure(response: Response) -> bool:
        return response.status_code in {401, 403}

    @staticmethod
    def _is_login_redirect(response: Response) -> bool:
        if not response.is_redirect:
            return False
        location = response.headers.get("Location", "").lower()
        return "login" in location or "permissionviolation" in location

    @staticmethod
    def _raise_for_response(method: str, path: str, response: Response) -> None:
        details = ""
        try:
            payload = response.json()
            messages = payload.get("errorMessages") if isinstance(payload, dict) else None
            errors = payload.get("errors") if isinstance(payload, dict) else None
            parts: list[str] = []
            if isinstance(messages, list):
                parts.extend(str(item) for item in messages)
            if isinstance(errors, dict):
                parts.extend(f"{key}: {value}" for key, value in errors.items())
            details = "; ".join(parts)
        except Exception:
            details = response.text[:500]
        raise RuntimeError(f"Jira request failed: {response.status_code} {method} {path} {details}".strip())
