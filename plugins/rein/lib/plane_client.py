#!/usr/bin/env python3
"""Plane client with a write strategy measured per entity (D3).

Everything here follows from a probe against a live Plane 1.4.1 Community
instance (see the plan's Why/What-was-measured), not from the API docs:

  * work items and modules DO support an idempotent upsert: `POST` with a
    used `external_id` returns `409` and the body carries the existing
    `id` -- `POST` -> 201 | `409` -> read `id` -> `PATCH`.
  * projects do NOT: the name-uniqueness check runs before `external_id`
    (so a re-sync 409s with no `id`), `GET ?external_id=` does not filter
    on projects at all, and there is no `external_id` uniqueness either (a
    repeat `external_id` with a different name just creates a duplicate).
    So projects are upserted by listing once and matching client-side on
    `(external_id, external_source)`.
  * a freshly created Project 400s `"Modules are not enabled"` on
    `POST /modules/` until `PATCH {"module_view": true}` runs.
  * the instance rate-limits at `API_KEY_RATE_LIMIT=60/minute`, surfaced as
    `error_code 5900` -- alongside plain HTTP 429 -- and both need backoff.

This module reads no config file. The API key comes only from
`REIN_PLANE_API_KEY` (D4); there is no code path that reads a file for it.
No code path issues `DELETE` (D6) -- rein creates and updates, never
deletes, in Plane.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

EXTERNAL_SOURCE = "rein"
API_KEY_ENV = "REIN_PLANE_API_KEY"

# Measured: Project.FORBIDDEN_IDENTIFIER_CHARS_PATTERN. Includes the hyphen,
# so almost no repo name is legal without cleaning first (D9).
FORBIDDEN_NAME_CHARS = "&+,:;$^}{*=?@#|'<>.()%!-"

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 1.0
RATE_LIMIT_ERROR_CODE = 5900


class PlaneError(Exception):
    """Base class for every error this client raises."""


class PlaneAuthError(PlaneError):
    """`REIN_PLANE_API_KEY` is unset. The key is never read from a file (D4)."""


class PlaneConflictError(PlaneError):
    """A `409`/`400` whose body carried no `id` to resolve against.

    Plane reports identifier collisions under the wrong field -- e.g.
    `{"name": "The project name is already taken"}` even when the real
    collision is on `identifier` or `external_id` -- so this error names
    the entity kind and the attempted name itself instead of repeating
    Plane's misdirected message as if it explained the failure.
    """

    def __init__(self, entity_kind: str, name: str, body):
        self.entity_kind = entity_kind
        self.name = name
        self.body = body
        super().__init__(
            f"conflict creating {entity_kind} {name!r}: Plane returned no id "
            f"to resolve against (body={body!r})"
        )


class PlaneRetryExhausted(PlaneError):
    """Backoff on rate limiting (HTTP 429 or `error_code 5900`) gave up."""

    def __init__(self, method: str, path: str, attempts: int):
        self.method = method
        self.path = path
        self.attempts = attempts
        super().__init__(f"{method} {path} still rate-limited after {attempts} attempts")


class PlaneRequestError(PlaneError):
    """Any other non-2xx response this client does not know how to recover from."""

    def __init__(self, method: str, path: str, status: int, body):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} -> {status}: {body!r}")


def safe_name(name: str) -> str:
    """Strip the measured forbidden character set. Mechanical, no agent (D9)."""
    cleaned = "".join(ch for ch in (name or "") if ch not in FORBIDDEN_NAME_CHARS)
    cleaned = cleaned.strip()
    return cleaned or "untitled"


def safe_identifier(name: str) -> str:
    """At most 12 chars: an 8-char stem plus a 4-char hash of the FULL name.

    Plane's `identifier` is `max_length=12`, unique per workspace. Truncating
    a cleaned name to 12 chars alone collides whenever two names only differ
    past that point (`proxima-website`, `-v2`, `-v3`, `2` all reduce to the
    same first characters). Hashing the *full*, unsanitised name into the
    last 4 characters keeps every such family distinct (D9).
    """
    alnum = "".join(ch for ch in (name or "") if ch.isalnum()).upper()
    stem = alnum[:8] or "X"
    digest = hashlib.sha256((name or "").encode("utf-8")).hexdigest()[:4].upper()
    return (stem + digest)[:12]


def make_external_id(workspace: str, repo: str, change: str, task: str) -> str:
    """`rein:{workspace}:{repo}:{change}:{task}`, every component escaped.

    `%` and `:` are percent-escaped in each component before joining, so a
    component that itself contains `:` cannot shift the join and forge
    another entity's identity -- e.g. `repo="a:b"` under `workspace="ws"`
    would otherwise join identically to `repo="b"` under `workspace="ws:a"`.
    """

    def esc(part) -> str:
        return str(part).replace("%", "%25").replace(":", "%3A")

    return "rein:" + ":".join(esc(p) for p in (workspace, repo, change, task))


class PlaneTransport:
    """Real HTTP over `urllib`. Constructed lazily, only when a `PlaneClient`
    is built with no transport override -- every test in this suite injects
    its own, so this class is never instantiated during the test run."""

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout

    def request(self, method: str, url: str, headers: dict, body):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()


def _parse_json(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return {}


class PlaneClient:
    """Writes projects, modules and work items to one Plane workspace.

    Never reads Plane as a source of truth: the only reads are the ones a
    write needs (listing projects to match `external_id`, per D3). Never
    issues `DELETE` (D6).
    """

    def __init__(
        self,
        base_url: str,
        workspace_slug: str,
        *,
        transport=None,
        sleep=None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay: float = DEFAULT_BASE_DELAY,
        env=None,
        api_key_env: str = API_KEY_ENV,
    ):
        env = env if env is not None else os.environ
        api_key = env.get(api_key_env)
        if not api_key:
            raise PlaneAuthError(
                f"{api_key_env} is not set -- the API key is never read from a file (D4)"
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._workspace_slug = workspace_slug
        self._transport_override = transport
        self._sleep = sleep or time.sleep
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._project_cache = None  # populated lazily by list_projects()

    @property
    def transport(self):
        if self._transport_override is None:
            self._transport_override = PlaneTransport()
        return self._transport_override

    # -- low-level request/retry ------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _request(self, method: str, path: str, json_body=None):
        headers = {"X-Api-Key": self._api_key, "Content-Type": "application/json"}
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        attempt = 0
        while True:
            attempt += 1
            status, _headers, raw = self.transport.request(method, self._url(path), headers, body)
            parsed = _parse_json(raw)
            retryable = status == 429 or (
                isinstance(parsed, dict) and parsed.get("error_code") == RATE_LIMIT_ERROR_CODE
            )
            if retryable:
                if attempt >= self._max_attempts:
                    raise PlaneRetryExhausted(method, path, attempt)
                self._sleep(self._base_delay * (2 ** (attempt - 1)))
                continue
            return status, parsed

    # -- work items & modules: measured 409-with-id upsert -----------------

    def _project_path(self, project_id: str) -> str:
        return f"/api/v1/workspaces/{self._workspace_slug}/projects/{project_id}"

    def _upsert_post_then_conflict(self, collection_path: str, payload: dict, entity_kind: str, name: str):
        status, parsed = self._request("POST", collection_path, json_body=payload)
        if status == 201:
            return parsed
        if status in (409, 400):
            existing_id = parsed.get("id") if isinstance(parsed, dict) else None
            if not existing_id:
                raise PlaneConflictError(entity_kind, name, parsed)
            patch_path = f"{collection_path}{existing_id}/"
            patch_status, patch_parsed = self._request("PATCH", patch_path, json_body=payload)
            if patch_status not in (200, 201):
                raise PlaneRequestError("PATCH", patch_path, patch_status, patch_parsed)
            return patch_parsed
        raise PlaneRequestError("POST", collection_path, status, parsed)

    def upsert_work_item(self, project_id: str, name: str, external_id: str, **fields) -> dict:
        payload = {"name": name, "external_id": external_id, "external_source": EXTERNAL_SOURCE, **fields}
        path = f"{self._project_path(project_id)}/issues/"
        return self._upsert_post_then_conflict(path, payload, "work item", name)

    def upsert_module(self, project_id: str, name: str, external_id: str, **fields) -> dict:
        payload = {"name": name, "external_id": external_id, "external_source": EXTERNAL_SOURCE, **fields}
        path = f"{self._project_path(project_id)}/modules/"
        return self._upsert_post_then_conflict(path, payload, "module", name)

    def attach_work_item_to_module(self, project_id: str, module_id: str, work_item_id: str) -> dict:
        """`POST /modules/{id}/module-issues/` -- creation alone never links
        a work item to a module; this is a separate call every time."""
        path = f"{self._project_path(project_id)}/modules/{module_id}/module-issues/"
        status, parsed = self._request("POST", path, json_body={"issues": [work_item_id]})
        if status not in (200, 201):
            raise PlaneRequestError("POST", path, status, parsed)
        return parsed

    # -- projects: list-and-match client side (D3) --------------------------

    def list_projects(self, force_refresh: bool = False) -> list:
        if self._project_cache is None or force_refresh:
            path = f"/api/v1/workspaces/{self._workspace_slug}/projects/"
            status, parsed = self._request("GET", path)
            if status != 200:
                raise PlaneRequestError("GET", path, status, parsed)
            items = parsed.get("results", []) if isinstance(parsed, dict) else parsed
            self._project_cache = list(items) if isinstance(items, list) else []
        return self._project_cache

    def _match_project(self, external_id: str):
        for project in self.list_projects():
            if project.get("external_id") == external_id and project.get("external_source") == EXTERNAL_SOURCE:
                return project
        return None

    def upsert_project(self, name: str, external_id: str, **fields) -> dict:
        """List-and-match, never the 409-with-id upsert (D3).

        Projects were measured to have none of the three identity
        mechanisms work items/modules get: the name-uniqueness check runs
        *before* `external_id` and 409s with no `id`, `GET ?external_id=`
        does not filter at all, and there is no `external_id` uniqueness
        either. So a match is found by listing once and comparing
        client-side; a POST that still 409s (name taken by something rein
        did not create) can never be resolved to an id and is surfaced as a
        named conflict, never read as one.
        """
        clean_name = safe_name(name)
        identifier = safe_identifier(name)
        payload = {
            "name": clean_name,
            "identifier": identifier,
            "external_id": external_id,
            "external_source": EXTERNAL_SOURCE,
            **fields,
        }
        collection_path = f"/api/v1/workspaces/{self._workspace_slug}/projects/"
        match = self._match_project(external_id)
        if match is not None:
            project_id = match["id"]
            patch_path = f"{collection_path}{project_id}/"
            status, parsed = self._request("PATCH", patch_path, json_body=payload)
            if status not in (200, 201):
                raise PlaneRequestError("PATCH", patch_path, status, parsed)
            self._project_cache = [parsed if p.get("id") == project_id else p for p in self._project_cache]
            return parsed

        status, parsed = self._request("POST", collection_path, json_body=payload)
        if status == 201:
            if self._project_cache is not None:
                self._project_cache.append(parsed)
            return parsed
        # Measured: `409 {"name": "The project name is already taken"}` --
        # no `id` in the body, ever, for a project. Do not attempt to read
        # one; surface a named conflict instead (D3).
        raise PlaneConflictError("project", clean_name, parsed)

    def ensure_project(self, name: str, external_id: str, **fields) -> dict:
        """`upsert_project()` then `PATCH {"module_view": true}`.

        Measured: a freshly created Project 400s `"Modules are not
        enabled"` on `POST /modules/` until this PATCH runs. Always
        re-issued (harmless on an already-enabled project) so the ordering
        holds regardless of whether this call created or matched.
        """
        project = self.upsert_project(name, external_id, **fields)
        project_id = project["id"]
        patch_path = f"/api/v1/workspaces/{self._workspace_slug}/projects/{project_id}/"
        status, parsed = self._request("PATCH", patch_path, json_body={"module_view": True})
        if status not in (200, 201):
            raise PlaneRequestError("PATCH", patch_path, status, parsed)
        return project
