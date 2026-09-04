import json
import threading
from urllib.request import urlopen

from fastdyn.activity_monitor import activity_log_path, activity_snapshot, create_activity_server


def test_activity_snapshot_summarizes_live_jsonl(tmp_path):
    log_path = activity_log_path(tmp_path)
    log_path.parent.mkdir(parents=True)
    records = [
        {
            "time_ns": 10,
            "rtos": "FreeRTOS",
            "event": "task_created",
            "task": "0x20000010",
            "task_name": "worker",
            "priority": 2,
        },
        {
            "time_ns": 20,
            "rtos": "FreeRTOS",
            "event": "task_switch",
            "task": "0x20000010",
            "task_name": "worker",
            "priority": 2,
        },
        {"unfinished": True},
    ]
    log_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n{", encoding="utf-8"
    )

    snapshot = activity_snapshot(log_path)

    assert len(snapshot.events) == 3
    assert snapshot.summary["event_count"] == 3
    assert snapshot.summary["rtoses"] == {"FreeRTOS": 2, "Unknown": 1}
    assert snapshot.summary["tasks"] == [{
        "task": "0x20000010",
        "events": 2,
        "last_event": "task_switch",
        "rtos": "FreeRTOS",
        "time_ns": 20,
        "task_name": "worker",
        "priority": 2,
    }]


def test_activity_server_exposes_a_browser_api(tmp_path):
    log_path = activity_log_path(tmp_path)
    log_path.parent.mkdir(parents=True)
    log_path.write_text('{"time_ns":1,"rtos":"NuttX","event":"scheduler event"}\n')
    server = create_activity_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/activity") as response:
            payload = json.load(response)
        assert payload["available"] is True
        assert payload["summary"]["rtoses"] == {"NuttX": 1}
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            assert b"FastDyn Activity Monitor" in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
