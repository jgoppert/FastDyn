"""Local browser activity monitor for RTOS introspection event artifacts.

The monitor deliberately consumes the JSONL file emitted by the native
introspection callbacks.  It has no knowledge of RTOS hook names, QEMU command
construction, or virtual registration, so it remains a normal FastDyn
frontend feature rather than a BoardRunner concern.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse
import webbrowser


DEFAULT_PORT = 8765
MAX_EVENTS = 1_000


@dataclass(frozen=True)
class ActivitySnapshot:
    """A browser-ready view of one introspection activity stream."""

    events: list[dict[str, Any]]
    summary: dict[str, Any]


def activity_log_path(run_dir: Path | str) -> Path:
    """Return the stable activity artifact location for a FastDyn run."""
    return Path(run_dir).expanduser().resolve() / "run-artifacts" / "introspection" / "activity.jsonl"


def read_activity_events(path: Path | str, limit: int = MAX_EVENTS) -> list[dict[str, Any]]:
    """Read valid records from a possibly-live JSONL artifact.

    A callback can be writing the final line while the browser polls.  Invalid
    or incomplete lines are ignored and become visible on the next refresh.
    """
    if limit < 1:
        return []
    event_path = Path(path)
    if not event_path.is_file():
        return []

    events: list[dict[str, Any]] = []
    with event_path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    return events[-limit:]


def activity_snapshot(path: Path | str, limit: int = MAX_EVENTS) -> ActivitySnapshot:
    """Summarize activity without imposing RTOS-specific task layouts."""
    events = read_activity_events(path, limit)
    by_rtos = Counter(str(event.get("rtos", "Unknown")) for event in events)
    by_kind = Counter(str(event.get("event", "unknown")) for event in events)
    tasks: dict[str, dict[str, Any]] = {}

    for event in events:
        address = event.get("task")
        if not address:
            continue
        task = tasks.setdefault(str(address), {"task": address, "events": 0})
        task["events"] += 1
        task["last_event"] = event.get("event", "unknown")
        task["rtos"] = event.get("rtos", "Unknown")
        task["time_ns"] = event.get("time_ns", 0)
        if event.get("task_name"):
            task["task_name"] = event["task_name"]
        if event.get("priority") is not None:
            task["priority"] = event["priority"]

    return ActivitySnapshot(
        events=events,
        summary={
            "event_count": len(events),
            "rtoses": dict(sorted(by_rtos.items())),
            "event_types": dict(sorted(by_kind.items())),
            "tasks": sorted(tasks.values(), key=lambda task: task["events"], reverse=True),
        },
    )


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FastDyn Activity Monitor</title><style>
:root { color-scheme: dark; font-family: ui-sans-serif, system-ui, sans-serif; background:#10131a; color:#e6edf7 }
body { max-width:1200px; margin:0 auto; padding:24px } h1 { margin-bottom:4px } .muted { color:#9ca9bd }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; margin:20px 0 }
.card { background:#1a2030; border:1px solid #2c374d; border-radius:8px; padding:14px } .value { font-size:1.7rem; font-weight:650 }
table { border-collapse:collapse; width:100%; background:#151b28; margin:16px 0 } th,td { padding:9px; text-align:left; border-bottom:1px solid #2c374d; font-family:ui-monospace,monospace; font-size:.86rem }
th { color:#a9c7ff; font-family:ui-sans-serif,system-ui } #state { float:right; color:#6ee7b7 }
</style></head><body><span id="state">connecting</span><h1>FastDyn Activity Monitor</h1>
<p class="muted">Live RTOS introspection activity. Refreshes once a second.</p><section class="grid" id="cards"></section>
<h2>Active tasks</h2><div id="tasks"></div><h2>Recent events</h2><div id="events"></div>
<script>
const esc = value => String(value ?? '').replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const table = (headers, rows) => `<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
async function refresh() { try { const payload = await (await fetch('/api/activity?limit=250')).json(); const s=payload.summary;
 document.querySelector('#state').textContent = payload.available ? 'live' : 'waiting for activity log';
 document.querySelector('#cards').innerHTML = `<div class="card"><div class="muted">events</div><div class="value">${s.event_count}</div></div><div class="card"><div class="muted">RTOS</div><div>${Object.keys(s.rtoses).map(esc).join('<br>') || '—'}</div></div><div class="card"><div class="muted">event types</div><div>${Object.entries(s.event_types).map(([k,v])=>`${esc(k)}: ${v}`).join('<br>') || '—'}</div></div>`;
 document.querySelector('#tasks').innerHTML = table(['task','name','priority','events','last event','sim time (ns)'], s.tasks.map(t=>[t.task,t.task_name||'—',t.priority??'—',t.events,t.last_event,t.time_ns]));
 document.querySelector('#events').innerHTML = table(['sim time (ns)','RTOS','event','task','name','priority'], payload.events.slice().reverse().map(e=>[e.time_ns,e.rtos,e.event,e.task||'—',e.task_name||'—',e.priority??'—']));
 } catch (error) { document.querySelector('#state').textContent = 'disconnected'; } }
refresh(); setInterval(refresh, 1000);
</script></body></html>"""


def _handler_for(log_path: Path):
    class ActivityHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            # The monitor should not add a request log line every second to a
            # user’s run output.
            return

        def _send_json(self, value: dict[str, Any]) -> None:
            body = json.dumps(value, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - required stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/api/activity":
                requested = parse_qs(parsed.query).get("limit", [str(MAX_EVENTS)])[0]
                try:
                    limit = min(max(int(requested), 1), MAX_EVENTS)
                except ValueError:
                    limit = MAX_EVENTS
                snapshot = activity_snapshot(log_path, limit)
                self._send_json({
                    "available": log_path.is_file(),
                    "path": str(log_path),
                    "events": snapshot.events,
                    "summary": snapshot.summary,
                })
                return
            if parsed.path in {"/", "/index.html"}:
                body = _PAGE.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    return ActivityHandler


def create_activity_server(run_dir: Path | str, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Create, but do not start, a local activity-monitor HTTP server."""
    return ThreadingHTTPServer((host, port), _handler_for(activity_log_path(run_dir)))


def start_activity_monitor(
    run_dir: Path | str,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Start a daemon monitor for the duration of a FastDyn run."""
    server = create_activity_server(run_dir, host, port)
    url = f"http://{host}:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, name="fastdyn-activity-monitor", daemon=True)
    thread.start()
    if open_browser:
        webbrowser.open(url)
    return server, thread, url
