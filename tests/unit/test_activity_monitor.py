import json
import threading
from urllib.request import urlopen

from fastdyn.introspect.activity_monitor import (
    activity_log_path,
    activity_snapshot,
    create_activity_server,
)


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
            "task_state": 3,
            "fields": {"base.sched_locked": 0, "base.timeout.dticks": 25},
        },
        {
            "time_ns": 25,
            "rtos": "FreeRTOS",
            "event": "resource_created",
            "resource": "0x20000100",
            "resource_type": "semaphore",
            "state": "available",
            "fields": {"count": 1, "limit": 3},
        },
        {
            "time_ns": 20,
            "rtos": "FreeRTOS",
            "event": "task_switch",
            "task": "0x20000010",
            "task_name": "worker",
            "priority": 2,
            "task_state": 3,
        },
        {"unfinished": True},
    ]
    log_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n{", encoding="utf-8"
    )

    snapshot = activity_snapshot(log_path)

    assert len(snapshot.events) == 4
    assert snapshot.summary["event_count"] == 4
    assert snapshot.summary["rtoses"] == {"FreeRTOS": 3, "Unknown": 1}
    assert snapshot.summary["tasks"] == [{
        "task": "0x20000010",
        "events": 2,
        "last_event": "task_switch",
        "rtos": "FreeRTOS",
        "time_ns": 20,
        "task_name": "worker",
        "priority": 2,
        "state": 3,
        "fields": {"base.sched_locked": 0, "base.timeout.dticks": 25},
    }]
    assert snapshot.summary["resources"] == [{
        "resource": "0x20000100",
        "events": 1,
        "last_event": "resource_created",
        "rtos": "FreeRTOS",
        "resource_type": "semaphore",
        "time_ns": 25,
        "state": "available",
        "fields": {"count": 1, "limit": 3},
    }]
    assert snapshot.summary["active_task"] == {
        "task": "0x20000010",
        "task_name": "worker",
        "priority": 2,
        "state": 3,
        "rtos": "FreeRTOS",
        "time_ns": 20,
        "fields": {"base.sched_locked": 0, "base.timeout.dticks": 25},
    }


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
            page = response.read()
        assert b"FastDyn Introspection Activity" in page
        assert b"active task" in page
        assert b"const $=" in page
        assert b"Stable watch" in page
        assert b"events / bucket" in page
        assert b'viewBox="0 0 1000 240"' in page
        assert b"Task CPU ownership" in page
        assert b"Kernel resources" in page
        assert b"TCB inspector" in page
        assert b"values update from new observations" in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_activity_snapshot_estimates_task_ownership_from_switch_intervals(tmp_path):
    log_path = activity_log_path(tmp_path)
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(json.dumps(record) for record in [
            {"time_ns": 10, "event": "task_switch", "task": "0x1", "task_name": "one"},
            {"time_ns": 20, "event": "resource_created", "resource": "0x10"},
            {"time_ns": 30, "event": "task_switch", "task": "0x2", "task_name": "two"},
            {"time_ns": 50, "event": "task_created", "task": "0x2"},
        ]),
        encoding="utf-8",
    )

    summary = activity_snapshot(log_path).summary

    assert summary["time_span_ns"] == 40
    assert summary["cpu_ownership"] == [
        {"task": "0x1", "task_name": "one", "duration_ns": 20, "percent": 50.0},
        {"task": "0x2", "task_name": "two", "duration_ns": 20, "percent": 50.0},
    ]
