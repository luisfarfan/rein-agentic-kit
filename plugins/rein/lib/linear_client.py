#!/usr/bin/env python3
"""Linear GraphQL client. Linear is the source; nothing else is read.

The API key comes only from `REIN_LINEAR_API_KEY` -- there is no code path
that reads it from a file, and none that writes it anywhere. No code path
issues a delete or an archive: this client reads, moves an issue's state,
and comments. That is the whole surface.

Measured against the live `pproxima` workspace, not taken from the docs:

  * a personal `lin_api_...` key goes in `Authorization` with **no**
    `Bearer` prefix. With the prefix the API answers 400, not 401, which
    reads like a malformed query rather than a credential problem.
  * the official MCP server (`https://mcp.linear.app/mcp`) is OAuth-only
    and rejects this key outright, which is why this module talks to
    `api.linear.app/graphql` directly rather than through it.
  * `identifier` (`PPR-86`) is the human handle and the only id a person
    ever types; the mutations need the `id` (a uuid). Every lookup here
    therefore accepts the identifier and resolves it, so no caller has to
    hold a uuid.

Rate limits are a documented 1,500 requests/hour for a personal key, with
complexity counted per query -- far above anything an intake does, but a
429 is still retried with backoff rather than surfaced as a crash, and
`Retry-After` is honoured when the response carries it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API_URL = "https://api.linear.app/graphql"
API_KEY_ENV = "REIN_LINEAR_API_KEY"

MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 1.0


class LinearError(Exception):
    """Base for everything this module raises."""


class LinearAuthError(LinearError):
    """No key in the environment. Names the variable, because the fix is to
    set it and nothing in the message should send anyone hunting for a
    config file that does not exist."""

    def __init__(self, env_var: str = API_KEY_ENV):
        super().__init__(
            f"{env_var} is not set -- export it with a Linear personal API key "
            f"(Settings -> Security & access -> Personal API keys)"
        )


class LinearUnreachable(LinearError):
    """Network, DNS or TLS. Distinguished from a rejected query because the
    fix is completely different: one is the operator's connection, the
    other is this code."""

    def __init__(self, cause):
        super().__init__(f"cannot reach {API_URL}: {cause}")
        self.cause = cause


class LinearRequestError(LinearError):
    def __init__(self, status: int, body):
        super().__init__(f"Linear returned HTTP {status}: {_trim(body)}")
        self.status = status
        self.body = body


class LinearQueryError(LinearError):
    """A 200 carrying `errors`. GraphQL reports failure in the body, so
    treating any 200 as success silently turns a rejected query into an
    empty result set -- an intake that finds no issues rather than one that
    says why."""

    def __init__(self, errors):
        messages = "; ".join(
            e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors
        )
        super().__init__(f"Linear rejected the query: {messages}")
        self.errors = errors


class IssueNotFound(LinearError):
    def __init__(self, identifier: str):
        super().__init__(f"no issue {identifier!r} in this workspace")
        self.identifier = identifier


def _trim(value, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


class Transport:
    """Isolated so tests drive this client without a network. The real one
    is the only place `urllib` is touched."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def post(self, url: str, headers: dict, body: bytes):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise LinearUnreachable(exc) from exc


class LinearClient:
    def __init__(self, transport=None, env=None, sleep=time.sleep):
        env = os.environ if env is None else env
        key = (env.get(API_KEY_ENV) or "").strip()
        if not key:
            raise LinearAuthError()
        self._key = key
        self._transport = transport or Transport()
        self._sleep = sleep

    # ------------------------------------------------------------ transport

    def execute(self, query: str, variables: dict | None = None) -> dict:
        """One GraphQL round trip, retried on 429 and 5xx.

        A 4xx other than 429 is NOT retried: a malformed query or a revoked
        key returns the same answer every time, and retrying it just makes
        the operator wait four times as long for the same message.
        """
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        headers = {
            "Authorization": self._key,
            "Content-Type": "application/json",
        }
        last = None
        for attempt in range(MAX_ATTEMPTS):
            status, response_headers, raw = self._transport.post(API_URL, headers, payload)
            if status == 200:
                try:
                    body = json.loads(raw)
                except ValueError as exc:
                    raise LinearRequestError(status, f"unparseable body: {exc}") from exc
                if body.get("errors"):
                    raise LinearQueryError(body["errors"])
                return body.get("data") or {}
            last = LinearRequestError(status, raw)
            if status != 429 and status < 500:
                raise last
            if attempt == MAX_ATTEMPTS - 1:
                break
            self._sleep(self._delay(response_headers, attempt))
        raise last

    def _delay(self, headers: dict, attempt: int) -> float:
        """`Retry-After` when the server sends one, else exponential backoff.

        The server knows when its window reopens and this code does not, so
        an honoured header beats a guess every time.
        """
        for name, value in (headers or {}).items():
            if name.lower() == "retry-after":
                try:
                    return max(0.0, float(value))
                except (TypeError, ValueError):
                    break
        return BACKOFF_BASE_SECONDS * (2 ** attempt)

    # ---------------------------------------------------------------- reads

    _ISSUE_FIELDS = """
        id identifier title description priority branchName url
        state { name type }
        labels { nodes { name } }
        parent { identifier title }
        assignee { name }
    """

    def issue(self, identifier: str) -> dict:
        """One issue by its human identifier (`PPR-86`)."""
        query = "query($id: String!) { issue(id: $id) { %s } }" % self._ISSUE_FIELDS
        try:
            data = self.execute(query, {"id": identifier})
        except LinearQueryError:
            raise IssueNotFound(identifier) from None
        found = data.get("issue")
        if not found:
            raise IssueNotFound(identifier)
        return found

    def issues(self, team_key: str = "", first: int = 250) -> list:
        """Every issue, paged. Filtering happens in `linear_select`, not
        here: the filters a person wants (by repo, by flow) are derived
        from labels and document text rather than being API fields, so a
        server-side filter could only ever cover half of them and the two
        halves would disagree about what "no results" means.
        """
        query = """
        query($after: String, $first: Int!) {
          issues(first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes { %s }
          }
        }
        """ % self._ISSUE_FIELDS
        out, cursor = [], None
        while True:
            data = self.execute(query, {"after": cursor, "first": first})
            block = data.get("issues") or {}
            out.extend(block.get("nodes") or [])
            page = block.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return out
            cursor = page.get("endCursor")
            if not cursor:
                return out

    def states(self, team_key: str = "") -> list:
        """The team's workflow states. `name` is what a person types and
        `type` is what actually classifies it, so both are returned: two
        teams can name the same `type` differently."""
        query = """
        query { workflowStates(first: 100) { nodes { id name type team { key } } } }
        """
        nodes = (self.execute(query).get("workflowStates") or {}).get("nodes") or []
        if team_key:
            nodes = [n for n in nodes if ((n.get("team") or {}).get("key") == team_key)]
        return nodes

    # --------------------------------------------------------------- writes

    def set_state(self, identifier: str, state_name: str) -> dict:
        """Move one issue to the named state.

        The state is resolved against the issue's OWN team rather than the
        first match in the workspace: state names repeat across teams, and
        moving an issue to another team's state id is an error the API
        reports only as a failed update.
        """
        target = self.issue(identifier)
        query = "query($id: String!) { issue(id: $id) { team { key } } }"
        team = (((self.execute(query, {"id": identifier}) or {}).get("issue") or {})
                .get("team") or {}).get("key") or ""
        wanted = state_name.strip().lower()
        match = next(
            (s for s in self.states(team) if (s.get("name") or "").lower() == wanted), None
        )
        if match is None:
            names = ", ".join(sorted((s.get("name") or "") for s in self.states(team)))
            raise LinearError(f"no state named {state_name!r} on team {team or '?'} -- have: {names}")
        mutation = """
        mutation($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: {stateId: $stateId}) {
            success issue { identifier state { name } }
          }
        }
        """
        data = self.execute(mutation, {"id": target["id"], "stateId": match["id"]})
        result = data.get("issueUpdate") or {}
        if not result.get("success"):
            raise LinearError(f"Linear refused to move {identifier} to {state_name!r}")
        return result.get("issue") or {}

    def comment(self, identifier: str, body: str) -> dict:
        """Leave a comment. The board carried ZERO comments when this was
        written, so a state change alone says what happened but never why
        or where: the branch and the commit live here."""
        target = self.issue(identifier)
        mutation = """
        mutation($issueId: String!, $body: String!) {
          commentCreate(input: {issueId: $issueId, body: $body}) {
            success comment { id url }
          }
        }
        """
        data = self.execute(mutation, {"issueId": target["id"], "body": body})
        result = data.get("commentCreate") or {}
        if not result.get("success"):
            raise LinearError(f"Linear refused to comment on {identifier}")
        return result.get("comment") or {}
