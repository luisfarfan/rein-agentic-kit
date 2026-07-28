#!/usr/bin/env python3
"""rein dashboard -- the run ledger as a view model, servable locally.

Usage:
    rein dashboard            serve the view model at http://127.0.0.1:8765/
    rein dashboard --port N   serve on a different port (still 127.0.0.1 only)
    rein dashboard --json     print the view model and exit -- no socket opened

The whole data path (ledger -> baseline join -> view model) is a pure function
of two files on disk, so it is testable without a network: `build_view` never
touches a socket, only `serve` does.

Local only, on purpose (see the plan's decisions): no auth, no sharing, no
live streaming -- the ledger is written when a run ends, and that is the only
moment this reads from.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from token_report import (  # noqa: E402
    BASELINE_PATH,
    LEDGER_PATH,
    BaselineCorruptError,
    _pct_change,
    read_baseline,
    read_ledger,
)

DEFAULT_PORT = 8765


# --------------------------------------------------------------- safe reads --


def _read_ledger_safe(ledger_path: str) -> tuple[list[dict], str]:
    """Rows plus an error message, never an exception.

    `read_ledger` already skips individual corrupt JSON lines while keeping the
    rest (that is the "partly corrupt" case -- valid rows must survive it). This
    only guards the outer failure modes a corrupt *file* can still cause (not a
    directory, not readable, not UTF-8) so those degrade to an empty ledger with
    a message instead of a traceback.
    """
    if not os.path.exists(ledger_path):
        return [], ""
    try:
        return read_ledger(ledger_path), ""
    except OSError as exc:
        return [], f"ledger unreadable at {ledger_path}: {exc}"
    except UnicodeDecodeError as exc:
        return [], f"ledger unreadable at {ledger_path}: {exc}"


def _read_baseline_safe(baseline_path: str) -> dict | None:
    """`None` for both "no baseline" and "baseline file is corrupt" -- a
    dashboard has no good way to surface a second error channel, and a corrupt
    baseline must never take down the run list with it."""
    try:
        return read_baseline(baseline_path)
    except BaselineCorruptError:
        return None


# ------------------------------------------------------------------ view model --


def _run_view(row: dict, project: str, baseline: dict | None) -> dict:
    baseline_wf_id = baseline.get("wf_id") if baseline else None
    baseline_project = baseline.get("project") if baseline else None
    baseline_ts = (baseline.get("ts") or "") if baseline else ""
    # A baseline marked before `project` was tracked cannot be matched to any
    # project safely -- never guess, suppress deltas entirely (mirrors
    # token_report.render_ledger's `baseline_stale`).
    baseline_stale = bool(baseline) and not baseline_project

    is_baseline = bool(baseline_wf_id) and row.get("wf_id") == baseline_wf_id and project == baseline_project

    deltas = None
    if (
        baseline
        and not baseline_stale
        and not is_baseline
        and project == baseline_project
        and row.get("ts", "") > baseline_ts
    ):
        deltas = {
            "turns_per_agent": _pct_change(row.get("turns_per_agent"), baseline.get("turns_per_agent")),
            "ctx_max": _pct_change(row.get("ctx_max"), baseline.get("ctx_max")),
            "opus_share": _pct_change(row.get("opus_share"), baseline.get("opus_share")),
        }

    return {
        "wf_id": row.get("wf_id", ""),
        "ts": row.get("ts", ""),
        "turns": row.get("turns", 0),
        "turns_per_agent": row.get("turns_per_agent", 0),
        "ctx_max": row.get("ctx_max", 0),
        "opus_share": row.get("opus_share", 0),
        "totals": row.get("totals", {}),
        "total": row.get("total", 0),
        "agents": row.get("agents", []),
        "is_baseline": is_baseline,
        "deltas": deltas,
    }


def build_view(ledger_path: str = LEDGER_PATH, baseline_path: str = BASELINE_PATH) -> dict:
    """Runs grouped by project, baseline marked, later-same-project runs deltad.

    Never raises: a missing, empty, or unreadable ledger yields
    `{"message": "...", "projects": []}` instead.
    """
    rows, ledger_error = _read_ledger_safe(ledger_path)
    if not rows:
        message = ledger_error or f"no runs recorded yet ({ledger_path})"
        return {"message": message, "projects": []}

    baseline = _read_baseline_safe(baseline_path)

    by_project: dict[str, list[dict]] = {}
    for row in rows:
        by_project.setdefault(row.get("project") or "(unknown)", []).append(row)

    projects = []
    for project, project_rows in sorted(by_project.items()):
        ordered = sorted(project_rows, key=lambda r: r.get("ts", ""))
        projects.append(
            {
                "project": project,
                "runs": [_run_view(r, project, baseline) for r in ordered],
            }
        )

    return {"message": "", "projects": projects}


# ---------------------------------------------------------------------- serve --


def _make_handler(view: dict) -> type[http.server.BaseHTTPRequestHandler]:
    body = json.dumps(view, indent=2, ensure_ascii=False).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # keep test/CLI output quiet -- this is not an access log

    return Handler


def make_server(view: dict, port: int) -> http.server.HTTPServer:
    """Bound to 127.0.0.1 only -- never 0.0.0.0. This is local-only by design."""
    return http.server.HTTPServer(("127.0.0.1", port), _make_handler(view))


def serve(view: dict, port: int) -> None:
    httpd = make_server(view, port)
    try:
        print(f"rein dashboard: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# ----------------------------------------------------------------------- cli --


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rein dashboard", description=__doc__)
    p.add_argument("--json", action="store_true", dest="as_json", help="print the view model and exit (no server)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to bind on 127.0.0.1 (default: {DEFAULT_PORT})")
    p.add_argument("--ledger", default=LEDGER_PATH, help=f"ledger path (default: {LEDGER_PATH})")
    p.add_argument("--baseline", default=BASELINE_PATH, help=f"baseline path (default: {BASELINE_PATH})")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    view = build_view(args.ledger, args.baseline)

    if args.as_json:
        print(json.dumps(view, indent=2, ensure_ascii=False))
        return 0

    serve(view, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
