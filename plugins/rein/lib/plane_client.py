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
import collections
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

EXTERNAL_SOURCE = "rein"
API_KEY_ENV = "REIN_PLANE_API_KEY"

# Measured: Project.FORBIDDEN_IDENTIFIER_CHARS_PATTERN. Includes the hyphen,
# so almost no repo name is legal without cleaning first (D9).
FORBIDDEN_NAME_CHARS = "&+,:;$^}{*=?@#|'<>.()%!-"

# The server window the instance was measured enforcing
# (`API_KEY_RATE_LIMIT=60/minute`). Every number below is derived from it.
RATE_WINDOW_SECONDS = 60.0
DEFAULT_RATE_LIMIT = 60
MAX_BACKOFF_SECONDS = 30.0
# 1+2+4+8+16+30+30 = 91s > the 60s window, so a throttled call can still
# recover. At 5 attempts the budget was 15s and recovery was impossible.
DEFAULT_MAX_ATTEMPTS = 7
DEFAULT_BASE_DELAY = 1.0
RATE_LIMIT_ERROR_CODE = 5900


class PlaneError(Exception):
    """Base class for every error this client raises."""


class PlaneConfigError(PlaneError):
    """`plane.json` is not usable as written."""


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

    def __init__(self, entity_kind: str, name: str, body, status: int = 409):
        self.entity_kind = entity_kind
        self.name = name
        self.body = body
        self.status = status
        # 409 means a name or identifier is taken; 400 means Plane refused
        # the payload. Same error type (T003), different first word, so a
        # failure line does not send anyone hunting for a duplicate that
        # was never there.
        what = "conflict creating" if status != 400 else "Plane rejected the"
        super().__init__(
            f"{what} {entity_kind} {name!r}: Plane returned no id "
            f"to resolve against (body={body!r})"
        )


class PlaneRetryExhausted(PlaneError):
    """Backoff on rate limiting (HTTP 429 or `error_code 5900`) gave up."""

    def __init__(self, method: str, path: str, attempts: int):
        self.method = method
        self.path = path
        self.attempts = attempts
        super().__init__(f"{method} {path} still rate-limited after {attempts} attempts")


class PlaneUnreachable(PlaneError):
    """Plane could not be reached at all: down, wrong `base_url`, DNS,
    TLS. T006 distinguishes three CONFIG failures carefully and left the
    most common runtime one to surface as a raw `URLError` traceback."""

    def __init__(self, url: str, cause):
        self.url = url
        self.cause = cause
        super().__init__(f"cannot reach Plane at {url}: {cause}")


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
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # URLError: refused/DNS/TLS. ValueError: an empty or
            # scheme-less `base_url` ("unknown url type"). Both are the
            # same fact to a caller -- Plane is not there.
            raise PlaneUnreachable(url, exc) from exc


def _checked_base_url(base_url: str) -> str:
    """Where the API key is allowed to be sent.

    D4 calls `plane.json` "nothing secret, safe to commit" -- so it is a
    low-trust file any pull request can edit, and `X-Api-Key` rides on
    every request to whatever host it names. Plain `http` is permitted
    only for loopback, where nothing leaves the machine; anything else
    must be `https`. This is the one place a config value decides where
    a secret travels.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise PlaneConfigError("base_url is empty in plane.json")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PlaneConfigError(
            f"base_url {url!r} must start with https:// (or http:// for localhost)"
        )
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        raise PlaneConfigError(
            f"base_url {url!r} uses plain http to a non-local host -- the API key "
            f"would travel in clear text; use https://"
        )
    return url


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
        rate_limit: int | None = DEFAULT_RATE_LIMIT,
        clock=None,
    ):
        env = env if env is not None else os.environ
        api_key = env.get(api_key_env)
        if not api_key:
            raise PlaneAuthError(
                f"{api_key_env} is not set -- the API key is never read from a file (D4)"
            )
        self._api_key = api_key
        self._base_url = _checked_base_url(base_url)
        self._workspace_slug = workspace_slug
        self._transport_override = transport
        self._sleep = sleep or time.sleep
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._rate_limit = rate_limit
        self._clock = clock or time.monotonic
        self._sent = collections.deque()
        self._project_cache = None  # populated lazily by list_projects()

    @property
    def transport(self):
        if self._transport_override is None:
            self._transport_override = PlaneTransport()
        return self._transport_override

    # -- low-level request/retry ------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _pace(self):
        """Stay under the server's window instead of discovering it by 429.

        Measured: the instance enforces `API_KEY_RATE_LIMIT=60/minute` over a
        sliding window. Firing as fast as the socket allows burns all 60
        permits in about three seconds, and from there every call throttles.
        Exponential backoff cannot recover from that -- 1+2+4+8 is 15s, and
        the oldest permit does not free for ~57s -- so a real sync of the
        measured 30-day window (~900-1200 calls) would spend two hours
        sleeping and fail most entities. Pacing is what makes D10's "9
        minutes" a real number rather than an arithmetic one.
        """
        if not self._rate_limit:
            return
        now = self._clock()
        cutoff = now - RATE_WINDOW_SECONDS
        while self._sent and self._sent[0] <= cutoff:
            self._sent.popleft()
        if len(self._sent) >= self._rate_limit:
            wait = (self._sent[0] + RATE_WINDOW_SECONDS) - now
            if wait > 0:
                self._sleep(wait)
            now = self._clock()
            cutoff = now - RATE_WINDOW_SECONDS
            while self._sent and self._sent[0] <= cutoff:
                self._sent.popleft()
        self._sent.append(now)

    @staticmethod
    def _retry_after(headers) -> float | None:
        """Plane sends `Retry-After` when it throttles. Obeying it beats
        guessing: the server knows when the window frees and we do not."""
        if not headers:
            return None
        for key, value in dict(headers).items():
            if str(key).lower() != "retry-after":
                continue
            try:
                return max(0.0, float(str(value).strip()))
            except (TypeError, ValueError):
                return None
        return None

    def _request(self, method: str, path: str, json_body=None):
        headers = {"X-Api-Key": self._api_key, "Content-Type": "application/json"}
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        attempt = 0
        while True:
            attempt += 1
            self._pace()
            status, resp_headers, raw = self.transport.request(
                method, self._url(path), headers, body
            )
            parsed = _parse_json(raw)
            retryable = status == 429 or (
                isinstance(parsed, dict) and parsed.get("error_code") == RATE_LIMIT_ERROR_CODE
            )
            if retryable:
                if attempt >= self._max_attempts:
                    raise PlaneRetryExhausted(method, path, attempt)
                # Capped per attempt, but the CUMULATIVE budget has to exceed
                # the server's window or the retries are theatre.
                delay = self._retry_after(resp_headers)
                if delay is None:
                    delay = min(
                        self._base_delay * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS
                    )
                self._sleep(delay)
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
                # Stays a named conflict error -- T003 requires that, and
                # the point of the criterion is that it is never a
                # KeyError. But 409 and 400 mean different things: one is
                # a name already taken, the other a payload Plane refused.
                # The status rides along so the failure line can say which
                # instead of sending the reader hunting for a duplicate
                # that does not exist.
                raise PlaneConflictError(entity_kind, name, parsed, status=status)
            patch_path = f"{collection_path}{existing_id}/"
            patch_status, patch_parsed = self._request("PATCH", patch_path, json_body=payload)
            if patch_status not in (200, 201):
                raise PlaneRequestError("PATCH", patch_path, patch_status, patch_parsed)
            # MEASURED against a live instance: Plane's module PATCH
            # serializer omits `id` -- the body opens with `name`,
            # `description`, `start_date` and never carries one, unlike the
            # POST. Callers need it (attaching work items to a module, for
            # one), so it is carried over from the 409 that just handed it to
            # us rather than read back out of a body that has none.
            #
            # Only a re-sync of an ALREADY-EXISTING entity reaches this line,
            # which is why 985 green tests and a first live run both missed
            # it: the fake transport returned whatever body the test author
            # imagined, and a first sync only ever takes the 201 path.
            if isinstance(patch_parsed, dict) and "id" not in patch_parsed:
                patch_parsed = {**patch_parsed, "id": existing_id}
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

    # -- workspace / state lookups: reads a write needs (T006) --------------

    def get_workspace(self) -> dict | None:
        """Does the workspace exist? `None` when it does not, never raised.

        Measured against a real instance, not assumed: **there is no
        `/api/v1/workspaces/{slug}/` endpoint.** `apps/api/plane/api/urls/`
        has no workspace module at all, so that path falls through to a
        session-authenticated route and answers `401` for a workspace that
        exists — which would have made every sync fail with "credentials
        were not provided" while the key was perfectly valid.

        The reachable existence check is the projects collection, which the
        sync needs anyway:

            GET /v1/workspaces/<real>/projects/   -> 200
            GET /v1/workspaces/<absent>/projects/ -> 403   (not 404)

        `403` is the absent case here, because Plane answers "you are not a
        member of this workspace" for a slug that has no workspace either.
        Both mean the same thing to rein: a human has to create it.
        """
        path = f"/api/v1/workspaces/{self._workspace_slug}/projects/"
        status, parsed = self._request("GET", path)
        if status in (403, 404):
            return None
        if status != 200:
            raise PlaneRequestError("GET", path, status, parsed)
        # This response IS the first page of the project listing the sync
        # needs next. Discarding it spent a second call out of a 60/minute
        # budget for nothing -- but only prime the cache when there is no
        # second page, or the cache would be a truncated listing, which is
        # exactly the bug the pagination fix above exists to remove.
        if isinstance(parsed, dict) and not parsed.get("next_page_results"):
            items = parsed.get("results")
            if isinstance(items, list):
                self._project_cache = list(items)
        return {"slug": self._workspace_slug}

    def list_states(self, project_id: str) -> list:
        """`GET .../states/` -- every workflow State of a project, each
        carrying the `group` (`backlog`/`unstarted`/`started`/`completed`/
        `cancelled`) T006 maps a task transition onto. A read a write needs
        (per the plan's Out-of-scope), not a second source of truth."""
        path = f"{self._project_path(project_id)}/states/"
        status, parsed = self._request("GET", path)
        if status != 200:
            raise PlaneRequestError("GET", path, status, parsed)
        items = parsed.get("results", []) if isinstance(parsed, dict) else parsed
        return list(items) if isinstance(items, list) else []

    # -- projects: list-and-match client side (D3) --------------------------

    def list_projects(self, force_refresh: bool = False) -> list:
        """EVERY page, not the first.

        Plane paginates list endpoints with a cursor at 100 rows. D3's whole
        project-identity strategy is client-side matching over this listing,
        so a workspace past 100 projects made `_match_project` return None
        for a project that exists -- and since the plan measured that
        projects have NO `external_id` uniqueness, the follow-up POST does
        not 409 on identity: it either collides on the name (failing that
        repo on every run, permanently) or creates a DUPLICATE project.
        """
        if self._project_cache is None or force_refresh:
            self._project_cache = self._collect_pages(
                f"/api/v1/workspaces/{self._workspace_slug}/projects/"
            )
        return self._project_cache

    def _collect_pages(self, path: str, max_pages: int = 200) -> list:
        collected, cursor, seen = [], None, set()
        for _ in range(max_pages):
            page_path = f"{path}?cursor={cursor}" if cursor else path
            status, parsed = self._request("GET", page_path)
            if status != 200:
                raise PlaneRequestError("GET", page_path, status, parsed)
            if not isinstance(parsed, dict):
                return list(parsed) if isinstance(parsed, list) else []
            items = parsed.get("results")
            collected.extend(items if isinstance(items, list) else [])
            if not parsed.get("next_page_results"):
                break
            cursor = parsed.get("next_cursor")
            # A server that keeps handing back the same cursor would spin
            # forever; stop rather than hang a sync on it.
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        return collected

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
