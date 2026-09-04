"""Local browser monitor for the RTOS introspection plugin."""
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
    events: list[dict[str, Any]]
    summary: dict[str, Any]

def activity_log_path(run_dir: Path | str) -> Path:
    return Path(run_dir).expanduser().resolve() / "run-artifacts" / "introspection" / "activity.jsonl"

def read_activity_events(path: Path | str, limit: int = MAX_EVENTS) -> list[dict[str, Any]]:
    if limit < 1 or not Path(path).is_file(): return []
    values = []
    with Path(path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try: value = json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(value, dict): values.append(value)
    return values[-limit:]

def _time_ns(event: dict[str, Any]) -> int | None:
    try: return int(event["time_ns"])
    except (KeyError, TypeError, ValueError): return None

def _cpu_ownership(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    timed = sorted(((t, e) for e in events if (t := _time_ns(e)) is not None), key=lambda item: item[0])
    own: Counter[str] = Counter(); names: dict[str, str] = {}; active = None; previous = None
    for time, event in timed:
        if active is not None and previous is not None and time >= previous: own[active] += time - previous
        if event.get("task") and event.get("task_name"): names[str(event["task"])] = str(event["task_name"])
        if event.get("event") == "task_switch" and event.get("task"): active = str(event["task"])
        previous = time
    total = sum(own.values()); span = timed[-1][0] - timed[0][0] if len(timed) > 1 else 0
    return ([{"task": task, "task_name": names.get(task), "duration_ns": duration,
              "percent": round(duration / total * 100, 2) if total else 0} for task, duration in own.most_common()], span)

def activity_snapshot(path: Path | str, limit: int = MAX_EVENTS) -> ActivitySnapshot:
    events = read_activity_events(path, limit); tasks: dict[str, dict[str, Any]] = {}; resources: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("task"):
            address = str(event["task"]); task = tasks.setdefault(address, {"task": event["task"], "events": 0})
            task.update(events=task["events"] + 1, last_event=event.get("event", "unknown"), rtos=event.get("rtos", "Unknown"), time_ns=event.get("time_ns", 0))
            for target, source in (("task_name", "task_name"), ("priority", "priority"), ("state", "task_state")):
                if event.get(source) is not None: task[target] = event[source]
            if isinstance(event.get("fields"), dict): task["fields"] = event["fields"]
        if event.get("resource"):
            address = str(event["resource"]); resource = resources.setdefault(address, {"resource": event["resource"], "events": 0})
            resource.update(events=resource["events"] + 1, last_event=event.get("event", "unknown"), rtos=event.get("rtos", "Unknown"), resource_type=event.get("resource_type", "resource"), time_ns=event.get("time_ns", 0))
            if event.get("state"): resource["state"] = event["state"]
            if isinstance(event.get("fields"), dict): resource["fields"] = event["fields"]
    active_task = None
    for event in sorted(events, key=lambda event: _time_ns(event) or -1):
        if event.get("event") == "task_switch" and event.get("task"):
            task = tasks[str(event["task"])]
            active_task = {"task": str(event["task"]), "task_name": event.get("task_name") or task.get("task_name"), "priority": event.get("priority", task.get("priority")), "state": event.get("task_state", task.get("state")), "fields": event.get("fields") or task.get("fields", {}), "rtos": event.get("rtos", "Unknown"), "time_ns": event.get("time_ns", 0)}
    ownership, span = _cpu_ownership(events)
    return ActivitySnapshot(events, {"event_count": len(events), "rtoses": dict(sorted(Counter(str(e.get("rtos", "Unknown")) for e in events).items())), "event_types": dict(sorted(Counter(str(e.get("event", "unknown")) for e in events).items())), "tasks": sorted(tasks.values(), key=lambda item: item["events"], reverse=True), "resources": sorted(resources.values(), key=lambda item: item["events"], reverse=True), "time_span_ns": span, "cpu_ownership": ownership, "active_task": active_task})

_PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FastDyn Introspection Activity</title><style>
:root{color-scheme:dark;font-family:system-ui;background:#0b1018;color:#e8eef9}body{margin:0;padding:18px clamp(18px,4vw,76px);min-width:760px}header{display:flex;justify-content:space-between}h1,h2{margin:0}.muted{color:#97a9c3}.small{font-size:.78rem}#state{color:#63e6a8}.controls,.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin:12px 0}.controls,details,.card{background:#141d2b;border:1px solid #27364d;border-radius:10px;padding:12px}label{font-size:.78rem;color:#a4b5cb;display:grid;gap:4px}input,select,button{font:inherit;color:inherit;background:#0e1622;border:1px solid #354863;border-radius:6px;padding:7px}button{cursor:pointer}.value{font-size:1.32rem;font-weight:700}.scroll{max-height:430px;min-height:180px;overflow:auto;resize:vertical}details{margin:12px 0}summary{cursor:pointer;font-weight:700;display:flex;justify-content:space-between}table{border-collapse:collapse;width:100%;margin-top:10px}th,td{padding:8px;text-align:left;border-bottom:1px solid #26354a;font:.78rem ui-monospace,monospace;white-space:nowrap}th{color:#a4c9ff;position:sticky;top:0;background:#141d2b}.click{cursor:pointer}.dot{width:8px;height:8px;border-radius:50%;display:inline-block}.bars{display:grid;gap:8px;margin-top:10px}.barrow{display:grid;grid-template-columns:minmax(130px,250px) 1fr 58px;gap:8px;font:.78rem ui-monospace,monospace}.bar{height:14px;background:#0c1420}.bar i{display:block;height:100%;background:linear-gradient(90deg,#65a5ff,#ac79ff)}svg{width:100%;height:auto;aspect-ratio:1000/240;display:block;background:#0c1420;border:1px solid #27364d;border-radius:8px;margin-top:10px}.workspace{display:grid;grid-template-columns:minmax(0,1fr) minmax(360px,32vw);gap:14px}.inspector{position:sticky;top:12px;resize:horizontal;overflow:auto;width:clamp(360px,32vw,620px)}#watch-panel{margin-top:12px}@media(max-width:1050px){.workspace{grid-template-columns:1fr}.inspector{position:static;width:auto;resize:vertical}}
</style></head><body><header><div><h1>RTOS Activity</h1><p class="muted">Interactive guest-runtime view; refreshes every second.</p></div><span id="state">connecting</span></header><section class="controls"><label>Search<input id="search"></label><label>RTOS<select id="rtos"><option value="">All RTOSes</option></select></label><label>Event type<select id="kind"><option value="">All events</option></select></label><label>Visible events<select id="limit"><option value="250">Last 250</option><option value="100">Last 100</option><option value="50">Last 50</option><option value="0">All loaded</option></select></label><label>Event filter<span id="selection">none</span><button id="clear">Clear filter</button></label></section><details open><summary>Overview <span class="muted small">Run and event-rate statistics</span></summary><section id="cards" class="grid"></section><h2>Event rate over simulated time</h2><svg id="timeline" viewBox="0 0 1000 240"></svg></details><div class="workspace"><main><details open><summary>Task CPU ownership <span class="muted small">Estimated guest time, not host CPU usage</span></summary><div id="cpu" class="bars"></div></details><details open><summary>Tasks <span id="taskcount"></span></summary><div id="tasks" class="scroll"></div></details><details open><summary>Kernel resources</summary><div id="resources" class="scroll"></div></details></main><aside><details open class="inspector"><summary>Object viewer <span class="muted small">stable TCB/resource watch</span></summary><label>Inspect object<select id="watch"><option value="">Choose a task or resource…</option></select></label><button id="watch-active">Watch current task</button><div id="watch-panel"><p class="muted">Choose a task or resource to create a stable object watch.</p></div></details></aside></div><details><summary>Event stream <span id="eventcount"></span></summary><div id="events" class="scroll"></div></details><script>
const $=x=>document.getElementById(x),S={p:null,task:null,res:null,watch:null,choices:''},esc=x=>String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),hue=x=>{let h=0;for(let c of String(x))h=(h*31+c.charCodeAt(0))>>>0;return h%360},color=x=>`hsl(${hue(x)} 72% 64%)`,tint=x=>`hsla(${hue(x)} 72% 48% / .22)`,n=x=>typeof x==='number'?x.toLocaleString():x==null?'—':String(x),taskName=x=>x?.task_name||'unnamed task',resourceName=(x,a)=>`${x?.resource_type||'resource'} #${Math.max(1,a.filter(y=>y.resource_type===x?.resource_type).findIndex(y=>y.resource===x?.resource)+1)}`;
function table(h,r){return r.length?`<table><thead><tr>${h.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${r.join('')}</tbody></table>`:'<p class="muted">No matching data.</p>'}function row(c,d='',k=''){return`<tr class="${d?'click':''}" ${d}${k?` style="background:${tint(k)};color:${color(k)}"`:''}>${c.map(x=>`<td>${x}</td>`).join('')}</tr>`}function state(x,r){if(x==null)return'—';if(r!=='Zephyr')return String(x);if(+x===0)return'ready';return(+x&128)?'in use':String(x)}function filtered(){let q=$('search').value.toLowerCase(),r=$('rtos').value,k=$('kind').value,l=+$('limit').value,e=S.p.events.filter(x=>(!q||Object.values(x).join(' ').toLowerCase().includes(q))&&(!r||x.rtos===r)&&(!k||x.event===k)&&(!S.task||x.task===S.task)&&(!S.res||x.resource===S.res));return l?e.slice(-l):e}
function chart(e){let a=e.map(x=>+x.time_ns||0),v=$('timeline'),L=76,R=970,T=20,B=184;if(a.length<2){v.innerHTML='<text x="500" y="120" text-anchor="middle" fill="#97a9c3">Awaiting timestamped events</text>';return}let lo=Math.min(...a),d=Math.max(1,Math.max(...a)-lo),b=Array(32).fill(0);a.forEach(x=>b[Math.min(31,Math.floor((x-lo)/d*32))]++);let m=Math.max(...b,1),g=Array.from({length:Math.min(5,m)+1},(_,i)=>{let p=i/Math.min(5,m),y=B-p*(B-T);return`<line x1="${L}" y1="${y}" x2="${R}" y2="${y}" stroke="#33445e"/><text x="${L-10}" y="${y+5}" text-anchor="end" fill="#a4b5cb">${Math.round(m*p)}</text>`}).join('');v.innerHTML=`${g}<line x1="${L}" y1="${B}" x2="${R}" y2="${B}" stroke="#9badc5"/><line x1="${L}" y1="${T}" x2="${L}" y2="${B}" stroke="#9badc5"/>${[0,.25,.5,.75,1].map(p=>`<text x="${L+p*(R-L)}" y="${B+25}" text-anchor="middle" fill="#a4b5cb">${((lo+p*d)/1e6).toFixed(1)} ms</text>`).join('')}<text x="500" y="232" text-anchor="middle" fill="#97a9c3">simulated time</text><text x="18" y="102" fill="#97a9c3" transform="rotate(-90 18 102)">events / bucket</text>`+b.map((x,i)=>`<rect x="${L+i*(R-L)/32+2}" y="${B-x/m*(B-T)}" width="${(R-L)/32-4}" height="${x/m*(B-T)}" fill="${color(i)}"/>`).join('')}
function options(s){return[...s.tasks.map(x=>[`t:${x.task}`,`Task · ${taskName(x)}`]),...s.resources.map(x=>[`r:${x.resource}`,`Resource · ${resourceName(x,s.resources)}`])]}function watch(s){let select=$('watch'),o=options(s),sig=JSON.stringify(o);if(sig!==S.choices&&document.activeElement!==select){S.choices=sig;select.innerHTML='<option value="">Choose a task or resource…</option>'+o.map(([id,label])=>`<option value="${esc(id)}">${esc(label)}</option>`).join('');select.value=S.watch||''}let item=S.watch?.startsWith('t:')?s.tasks.find(x=>`t:${x.task}`===S.watch):s.resources.find(x=>`r:${x.resource}`===S.watch),panel=$('watch-panel');if(!item){panel.innerHTML=S.watch?'<p class="muted">Watched object is outside the loaded activity window.</p>':'<p class="muted">Choose a task or resource to create a stable object watch.</p>';return}let task=S.watch.startsWith('t:'),fields=[['state',task?state(item.state,item.rtos):item.state||'—'],['priority',task?item.priority??'—':item.events],['last event',item.last_event],['simulated time',n(item.time_ns)],...Object.entries(item.fields||{})];panel.innerHTML=`<h2>${task?'TCB inspector · '+esc(taskName(item)):'Resource inspector · '+esc(resourceName(item,s.resources))}</h2><p class="muted small">Stable watch; values update from new observations.</p>${table(['field','value'],fields.map(x=>row([esc(x[0]),esc(n(x[1]))],'',item.task||item.resource)))}`}
function render(){if(!S.p)return;let s=S.p.summary,e=filtered(),scroll=Object.fromEntries(['tasks','resources','events'].map(id=>[id,$(id).scrollTop]));$('state').textContent=S.p.available?'live':'waiting';for(let [id,val,label] of [['rtos',Object.keys(s.rtoses),'All RTOSes'],['kind',Object.keys(s.event_types),'All events']]){let old=$(id).value;$(id).innerHTML=`<option value="">${label}</option>`+val.map(x=>`<option>${esc(x)}</option>`).join('');$(id).value=old}$('selection').textContent=S.task?taskName(s.tasks.find(x=>x.task===S.task)):S.res?resourceName(s.resources.find(x=>x.resource===S.res),s.resources):'none';$('cards').innerHTML=[['active task',taskName(s.active_task)],['matching events',e.length],['tasks seen',s.tasks.length],['resources seen',s.resources.length]].map(x=>`<div class="card"><div class="muted small">${x[0]}</div><div class="value">${esc(x[1])}</div></div>`).join('');chart(e);let ts=s.tasks.filter(x=>!S.task||x.task===S.task),rs=s.resources.filter(x=>!S.res||x.resource===S.res);$('taskcount').textContent=`${ts.length} shown`;$('tasks').innerHTML=table(['task','state','prio','events','last event'],ts.map(x=>row([esc(taskName(x)),esc(state(x.state,x.rtos)),x.priority??'—',x.events,esc(x.last_event)],`data-task="${esc(x.task)}"`,x.task)));$('resources').innerHTML=table(['resource','type','state','events','last event'],rs.map(x=>row([esc(resourceName(x,s.resources)),esc(x.resource_type),esc(x.state||'—'),x.events,esc(x.last_event)],`data-res="${esc(x.resource)}"`,x.resource)));$('cpu').innerHTML=s.cpu_ownership.map(x=>`<div class="barrow"><span>${esc(taskName(x))}</span><span class="bar"><i style="width:${x.percent}%"></i></span><span>${x.percent}%</span></div>`).join('');let recent=e.slice().reverse();$('eventcount').textContent=`${recent.length} shown`;$('events').innerHTML=table(['sim time','RTOS','event','task/resource'],recent.map(x=>row([n(x.time_ns),esc(x.rtos||'—'),esc(x.event||'—'),esc(x.task?taskName(x):x.resource_type||'—')],x.task?`data-task="${esc(x.task)}"`:x.resource?`data-res="${esc(x.resource)}"`:'',x.task||x.resource||x.event)));for(let [id,top] of Object.entries(scroll))$(id).scrollTop=top;watch(s)}
document.addEventListener('input',render);document.addEventListener('change',e=>{if(e.target.id==='watch'){S.watch=e.target.value||null;watch(S.p.summary)}else render()});document.addEventListener('click',e=>{if(e.target.id==='watch-active'){let a=S.p.summary.active_task;S.watch=a?`t:${a.task}`:null;$('watch').value=S.watch||'';watch(S.p.summary);return}let x=e.target.closest('[data-task],[data-res]');if(x){S.task=x.dataset.task||null;S.res=x.dataset.res||null;S.watch=x.dataset.task?`t:${x.dataset.task}`:`r:${x.dataset.res}`;render()}if(e.target.id==='clear'){S.task=S.res=null;render()}});async function refresh(){try{S.p=await(await fetch('/api/activity?limit=1000')).json();render()}catch(_){$('state').textContent='disconnected'}}refresh();setInterval(refresh,1000);
function chart(events){let svg=$('timeline'),W=Math.max(640,Math.round(svg.clientWidth||1000)),H=330,L=72,R=W-28,T=22,B=H-58;svg.style.height=`${H}px`;svg.style.aspectRatio='auto';svg.setAttribute('viewBox',`0 0 ${W} ${H}`);if(events.length<2){svg.innerHTML=`<text x="${W/2}" y="${H/2}" text-anchor="middle" fill="#97a9c3">Awaiting timestamped events</text>`;return}let times=events.map(x=>+x.time_ns||0),lo=Math.min(...times),hi=Math.max(...times),span=Math.max(1,hi-lo),count=Math.min(48,Math.max(20,Math.floor((R-L)/24))),bins=Array(count).fill(0);times.forEach(x=>bins[Math.min(count-1,Math.floor((x-lo)/span*count))]++);let peak=Math.max(...bins,1),steps=5,yMax=Math.max(steps,Math.ceil(peak/steps)*steps),bar=(R-L)/count,grid=Array.from({length:steps+1},(_,i)=>{let value=i*yMax/steps,y=B-i*(B-T)/steps;return`<line x1="${L}" y1="${y}" x2="${R}" y2="${y}" stroke="#314158" stroke-width="1"/><line x1="${L-5}" y1="${y}" x2="${L}" y2="${y}" stroke="#9badc5"/><text x="${L-10}" y="${y+4}" text-anchor="end" fill="#aebed4" font-size="12">${value}</text>`}).join(''),ticks=Array.from({length:6},(_,i)=>{let p=i/5,x=L+p*(R-L),label=((lo+p*span)/1e6).toFixed(1),anchor=i===0?'start':i===5?'end':'middle';return`<line x1="${x}" y1="${B}" x2="${x}" y2="${B+5}" stroke="#9badc5"/><text x="${x}" y="${B+23}" text-anchor="${anchor}" fill="#aebed4" font-size="12">${label} ms</text>`}).join('');svg.innerHTML=`${grid}<line x1="${L}" y1="${B}" x2="${R}" y2="${B}" stroke="#b7c7dd" stroke-width="1.4"/><line x1="${L}" y1="${T}" x2="${L}" y2="${B}" stroke="#b7c7dd" stroke-width="1.4"/>${ticks}<text x="${(L+R)/2}" y="${H-12}" text-anchor="middle" fill="#aebed4" font-size="13">simulated time</text><text x="17" y="${(T+B)/2}" text-anchor="middle" fill="#aebed4" font-size="13" transform="rotate(-90 17 ${(T+B)/2})">events / bucket</text>`+bins.map((value,i)=>{let height=value/yMax*(B-T);return`<rect x="${L+i*bar+2}" y="${B-height}" width="${Math.max(1,bar-4)}" height="${height}" fill="#4d9df5" opacity=".88"><title>${value} events</title></rect>`}).join('')}
</script></body></html>'''

def _handler_for(log_path: Path):
    class ActivityHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None: return
        def _send_json(self, value: dict[str, Any]) -> None:
            body = json.dumps(value, sort_keys=True).encode(); self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/activity":
                try: limit = min(max(int(parse_qs(parsed.query).get("limit", [str(MAX_EVENTS)])[0]), 1), MAX_EVENTS)
                except ValueError: limit = MAX_EVENTS
                snapshot = activity_snapshot(log_path, limit); self._send_json({"available": log_path.is_file(), "path": str(log_path), "events": snapshot.events, "summary": snapshot.summary}); return
            if parsed.path in {"/", "/index.html"}:
                body = _PAGE.encode(); self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            self.send_error(HTTPStatus.NOT_FOUND)
    return ActivityHandler

def create_activity_server(run_dir: Path | str, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _handler_for(activity_log_path(run_dir)))

def start_activity_monitor(run_dir: Path | str, host: str = "127.0.0.1", port: int = DEFAULT_PORT, open_browser: bool = False) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = create_activity_server(run_dir, host, port); url = f"http://{host}:{server.server_port}/"; thread = threading.Thread(target=server.serve_forever, name="fastdyn-activity-monitor", daemon=True); thread.start()
    if open_browser: webbrowser.open(url)
    return server, thread, url
