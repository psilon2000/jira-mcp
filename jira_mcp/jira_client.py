from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings


class JiraClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._auth_variants: list[dict[str, Any]] = []
        self._active_auth_index = 0
        self._setup_retries()
        self._setup_auth_variants()
        self._apply_auth_variant(0)

    def _setup_retries(self) -> None:
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
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _setup_auth_variants(self) -> None:
        mode = self._settings.auth_mode
        cookie = self._settings.cookie
        username = self._settings.username
        password = self._settings.password
        token = self._settings.token

        variants: list[dict[str, Any]] = []

        if mode in {"cookie", "auto"} and cookie:
            variants.append({"name": "cookie", "cookie": cookie})

        if mode in {"basic", "auto", "cookie"} and username and (password or token):
            variants.append({"name": "basic", "basic": (username, password or token)})

        if mode in {"bearer", "auto", "cookie"} and token:
            variants.append({"name": "bearer", "token": token})

        if not variants:
            variants.append({"name": "none"})

        self._auth_variants = variants

    def _apply_auth_variant(self, index: int) -> None:
        self._active_auth_index = max(0, min(index, len(self._auth_variants) - 1))
        variant = self._auth_variants[self._active_auth_index]

        self._session.auth = None
        self._session.headers.pop("Cookie", None)
        self._session.headers.pop("Authorization", None)

        if variant.get("cookie"):
            self._session.headers["Cookie"] = variant["cookie"]
        elif variant.get("basic"):
            self._session.auth = variant["basic"]
        elif variant.get("token"):
            self._session.headers["Authorization"] = f"Bearer {variant['token']}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self._settings.rest_root.rstrip('/')}/{path.lstrip('/')}"
        response = self._session.request(method, url, timeout=self._settings.timeout_sec, **kwargs)
        if response.ok:
            return response

        if response.status_code in {401, 403} and self._active_auth_index + 1 < len(self._auth_variants):
            self._apply_auth_variant(self._active_auth_index + 1)
            response = self._session.request(method, url, timeout=self._settings.timeout_sec, **kwargs)
            if response.ok:
                return response

        details = ""
        try:
            payload = response.json()
            messages = payload.get("errorMessages") if isinstance(payload, dict) else None
            errors = payload.get("errors") if isinstance(payload, dict) else None
            parts: list[str] = []
            if isinstance(messages, list):
                parts.extend(str(x) for x in messages)
            if isinstance(errors, dict):
                parts.extend(f"{k}: {v}" for k, v in errors.items())
            details = "; ".join(parts)
        except Exception:
            details = response.text[:500]

        raise RuntimeError(f"Jira request failed: {response.status_code} {method} {path} {details}".strip())

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
        }

    def search_issues(self, jql: str, fields: list[str] | None, limit: int) -> dict[str, Any]:
        max_results = max(1, min(limit, 200))
        params: dict[str, Any] = {
            "jql": jql,
            "startAt": 0,
            "maxResults": max_results,
        }
        if fields:
            params["fields"] = ",".join(fields)

        response = self._request("GET", "/search", params=params)
        payload = response.json()
        return {
            "status": "ok",
            "total": payload.get("total", 0),
            "count": len(payload.get("issues", []) or []),
            "issues": payload.get("issues", []),
        }

    def get_issue(self, issue_key: str, fields: list[str] | None, expand: list[str] | None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = ",".join(expand)
        response = self._request("GET", f"/issue/{issue_key}", params=params or None)
        return {"status": "ok", "issue": response.json()}

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
        return {
            "status": "ok",
            "issue_key": issue_key,
            "worklog_id": payload.get("id"),
            "time_spent_seconds": payload.get("timeSpentSeconds"),
        }

    def update_issue(self, issue_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        self._request("PUT", f"/issue/{issue_key}", json={"fields": fields})
        return {"status": "ok", "issue_key": issue_key, "updated_fields": sorted(fields.keys())}

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
        return {"status": "ok", "issue_key": issue_key, "transition_id": str(transition_id)}

    def add_comment(self, issue_key: str, comment: str) -> dict[str, Any]:
        response = self._request("POST", f"/issue/{issue_key}/comment", json={"body": comment})
        payload = response.json()
        return {"status": "ok", "issue_key": issue_key, "comment_id": payload.get("id")}
