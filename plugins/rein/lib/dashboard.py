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
import html
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


# -------------------------------------------------------------------- html --

# The page is entirely self-contained (D2): one inline <style>, no <script>,
# no external stylesheet/font/image, no fetch/XHR. Per-agent rows stay
# reachable without leaving the page via native <details>/<summary> --
# that needs zero JavaScript.

_PAGE_CSS = """
body{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:2rem;color:#1a1a1a;background:#fff}
h1{font-size:1.4rem}
h2{font-size:1.05rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.25rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
th,td{padding:.35rem .6rem;text-align:right;border-bottom:1px solid #eee;vertical-align:top}
th:first-child,td:first-child{text-align:left}
tr.baseline{background:#fffbe6}
.badge{display:inline-block;background:#f5a623;color:#1a1a1a;font-size:.65rem;font-weight:700;padding:.1rem .35rem;border-radius:.2rem}
.delta-better{color:#0a7a2f;font-weight:600}
.delta-worse{color:#b3261e;font-weight:600}
.delta-same,.delta-na{color:#888}
details{text-align:left}
summary{cursor:pointer;color:#555}
table.agents{margin:.4rem 0 0;font-size:.85em}
.empty{color:#666;font-style:italic;padding:1rem 0}
"""


def _fmt_num(value, digits: int = 1) -> str:
    """Render a number for the page; `0` (not a crash) for anything missing or
    non-numeric -- the view model already defaults missing fields to 0/"" so
    this is a display-only safety net, not the source of truth."""
    if value is None:
        return "0"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _delta_cell(pct: float | None) -> str:
    """Word the sign explicitly -- for these metrics lower is always better,
    so a bare signed number would not read unambiguously as an improvement.
    The word ("better"/"worse"/"unchanged") is in the visible text itself, not
    just a CSS class, so it survives even plain-text extraction of the page."""
    if pct is None:
        return '<span class="delta-na">n/a</span>'
    if pct < 0:
        cls, word = "delta-better", "better"
    elif pct > 0:
        cls, word = "delta-worse", "worse"
    else:
        cls, word = "delta-same", "unchanged"
    return f'<span class="{cls}">{pct:+.1f}% ({word})</span>'


def _agents_detail(agents: list[dict]) -> str:
    """Per-agent rows, reachable without leaving the page (native disclosure,
    no JS). An empty `agents` list still renders -- never an empty <table>
    that looks broken."""
    if not agents:
        return "<details><summary>0 agents</summary><p>no agents recorded for this run</p></details>"
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(a.get("file", ""))),
            html.escape(str(a.get("model", ""))),
            _fmt_num(a.get("turns"), 0),
            _fmt_num(a.get("total"), 0),
            _fmt_num(a.get("ctx_max"), 0),
        )
        for a in agents
    )
    return (
        f"<details><summary>{len(agents)} agent(s)</summary>"
        '<table class="agents"><thead><tr><th>file</th><th>model</th><th>turns</th>'
        f"<th>total</th><th>ctx_max</th></tr></thead><tbody>{rows}</tbody></table></details>"
    )


def _run_row(run: dict, show_deltas: bool) -> str:
    row_class = ' class="baseline"' if run["is_baseline"] else ""
    badge = ' <span class="badge">BASELINE</span>' if run["is_baseline"] else ""
    cells = (
        f"<td>{html.escape(str(run.get('ts', '')))}<br><small>{html.escape(str(run.get('wf_id', '')))}</small>{badge}</td>"
        f"<td>{_fmt_num(run.get('turns_per_agent'))}</td>"
        f"<td>{_fmt_num(run.get('ctx_max'), 0)}</td>"
        f"<td>{_fmt_num(run.get('opus_share'))}%</td>"
    )
    if show_deltas:
        deltas = run.get("deltas")
        cells += "".join(
            f"<td>{_delta_cell(deltas[key]) if deltas else '&mdash;'}</td>"
            for key in ("turns_per_agent", "ctx_max", "opus_share")
        )
    cells += f"<td>{_agents_detail(run.get('agents') or [])}</td>"
    return f"<tr{row_class}>{cells}</tr>"


def _project_section(project: dict) -> str:
    runs = project["runs"]
    # Deltas (and therefore the whole "savings" column group) only appear for a
    # project that actually has a baseline in it -- with none, this stays a
    # plain trend table, protecting D4 (no baseline -> no savings figure).
    show_deltas = any(r["is_baseline"] or r["deltas"] is not None for r in runs)

    header = "<tr><th>run</th><th>turns/agent</th><th>ctx_max/turn</th><th>opus share</th>"
    if show_deltas:
        header += "<th>Δ turns/agent</th><th>Δ ctx_max</th><th>Δ opus share</th>"
    header += "<th>agents</th></tr>"

    body = "".join(_run_row(r, show_deltas) for r in runs)
    return (
        f"<section><h2>{html.escape(project['project'])}</h2>"
        f"<table><thead>{header}</thead><tbody>{body}</tbody></table></section>"
    )


def render_html(view: dict) -> str:
    """The whole page: one self-contained HTML document, numbers embedded
    server-side (D2) -- there is nothing here for a browser to fetch."""
    if not view.get("projects"):
        message = view.get("message") or "no runs recorded yet"
        body = f'<p class="empty">{html.escape(message)}</p>'
    else:
        body = "".join(_project_section(p) for p in view["projects"])

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>rein dashboard</title>"
        f"<style>{_PAGE_CSS}</style></head>"
        f"<body><h1>rein dashboard</h1>{body}</body></html>"
    )


# ---------------------------------------------------------------------- serve --


def _make_handler(view: dict) -> type[http.server.BaseHTTPRequestHandler]:
    body = render_html(view).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
