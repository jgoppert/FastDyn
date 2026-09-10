"""Protocol tests for the co-simulation master client.

These drive utils/fastdyn_cosim.py against a scripted fake QMP server, so the
budget protocol is covered without building QEMU or booting a guest.
"""

import importlib.util
import json
import socket
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "fastdyn_cosim", REPO_ROOT / "utils" / "fastdyn_cosim.py"
)
fastdyn_cosim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fastdyn_cosim)

BudgetMaster = fastdyn_cosim.BudgetMaster
CosimError = fastdyn_cosim.CosimError


class FakeQmpServer:
    """A minimal QMP server that models the run-for budget protocol.

    `script` maps a command name to a callable taking (server, arguments) and
    returning the `return` payload; it may also push events.
    """

    def __init__(self, tmp_path, behaviour="normal"):
        self.path = str(tmp_path / "qmp.sock")
        self.behaviour = behaviour
        self.total_budget = 0
        self.current_time = 0
        self.overshoot = 0
        self.exact = False
        self.commands = []
        self._listener = socket.socket(socket.AF_UNIX)
        self._listener.bind(self.path)
        self._listener.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _ = self._listener.accept()
        stream = conn.makefile("rw")
        stream.write(json.dumps({"QMP": {"version": {"qemu": {"major": 10}},
                                         "capabilities": []}}) + "\n")
        stream.flush()
        try:
            while True:
                line = stream.readline()
                if not line:
                    return
                message = json.loads(line)
                name = message["execute"]
                arguments = message.get("arguments", {})
                self.commands.append(name)
                for payload in self._handle(name, arguments):
                    stream.write(json.dumps(payload) + "\n")
                    stream.flush()
        except (BrokenPipeError, ConnectionResetError, ValueError):
            return

    def _budget_payload(self):
        return {"return": {"totalbudget": self.total_budget,
                           "currenttime": self.current_time,
                           "exact": self.exact}}

    def _handle(self, name, arguments):
        if name == "qmp_capabilities":
            return [{"return": {}}]
        if name == "query-status":
            return [{"return": {"status": "paused", "running": False}}]
        if name == "query-budget":
            return [self._budget_payload()]
        if name == "set-budget-mode":
            self.exact = bool(arguments["exact"])
            if self.exact:
                self.overshoot = 0     # the whole point of the mode
            return [self._budget_payload()]
        if name == "run-for":
            before = self.current_time
            self.total_budget += arguments["budget"]
            if self.behaviour == "shutdown_midslice":
                return [{"event": "RESUME"}, {"return": {"totalbudget": self.total_budget,
                                                         "currenttime": before}},
                        {"event": "SHUTDOWN"}]
            if self.behaviour == "never_stops":
                return [{"event": "RESUME"}, {"return": {"totalbudget": self.total_budget,
                                                         "currenttime": before}}]
            self.current_time = self.total_budget + self.overshoot
            payloads = [{"event": "RESUME"},
                        {"return": {"totalbudget": self.total_budget,
                                    "currenttime": before,
                                    "exact": self.exact}},
                        {"event": "STOP"}]
            if self.behaviour == "stop_before_reply":
                payloads = [payloads[0], payloads[2], payloads[1]]
            return payloads
        return [{"error": {"class": "CommandNotFound", "desc": name}}]


def test_connect_performs_the_capabilities_handshake_and_reads_state(tmp_path):
    server = FakeQmpServer(tmp_path)
    with BudgetMaster(server.path) as master:
        assert master.time_ns == 0
        assert master.budget_ns == 0
    assert server.commands[:2] == ["qmp_capabilities", "query-budget"]


def test_run_slice_grants_budget_and_reports_the_time_reached(tmp_path):
    server = FakeQmpServer(tmp_path)
    with BudgetMaster(server.path) as master:
        assert master.run_slice(20_000_000) == 20_000_000
        assert master.run_slice(20_000_000) == 40_000_000
        assert master.budget_ns == 40_000_000


def test_overshoot_is_reported_rather_than_assumed(tmp_path):
    server = FakeQmpServer(tmp_path)
    server.overshoot = 228_128
    with BudgetMaster(server.path) as master:
        reached = master.run_slice(20_000_000)
    # The master must report where the guest actually stopped, not the deadline.
    assert reached == 20_228_128


def test_stop_arriving_before_the_reply_is_handled(tmp_path):
    server = FakeQmpServer(tmp_path, behaviour="stop_before_reply")
    with BudgetMaster(server.path) as master:
        assert master.run_slice(20_000_000) == 20_000_000


def test_guest_shutdown_midslice_is_a_clear_error(tmp_path):
    server = FakeQmpServer(tmp_path, behaviour="shutdown_midslice")
    with BudgetMaster(server.path) as master:
        with pytest.raises(CosimError, match="shut down before its budget"):
            master.run_slice(20_000_000)


def test_a_slice_that_never_completes_times_out_with_guidance(tmp_path):
    server = FakeQmpServer(tmp_path, behaviour="never_stops")
    with BudgetMaster(server.path, slice_timeout=0.4) as master:
        with pytest.raises(CosimError, match="no STOP event"):
            master.run_slice(20_000_000)


def test_missing_socket_names_the_likely_cause(tmp_path):
    master = BudgetMaster(str(tmp_path / "absent.sock"), connect_timeout=0.3)
    with pytest.raises(CosimError, match="stop_on_start"):
        master.connect()


def test_non_positive_slices_are_rejected(tmp_path):
    server = FakeQmpServer(tmp_path)
    with BudgetMaster(server.path) as master:
        with pytest.raises(ValueError):
            master.run_slice(0)


def test_exact_stop_is_requested_on_connect_when_asked(tmp_path):
    server = FakeQmpServer(tmp_path)
    server.overshoot = 150_000
    with BudgetMaster(server.path, exact_stop=True) as master:
        assert master.exact_stop is True
        # With the mode on, every slice lands precisely on its deadline.
        assert master.run_slice(500_000) == 500_000
        assert master.run_slice(500_000) == 1_000_000
    assert "set-budget-mode" in server.commands


def test_exact_stop_is_not_requested_by_default(tmp_path):
    server = FakeQmpServer(tmp_path)
    server.overshoot = 150_000
    with BudgetMaster(server.path) as master:
        assert master.exact_stop is False
        assert master.run_slice(500_000) == 650_000     # overshoot preserved
    assert "set-budget-mode" not in server.commands


def test_mode_reported_by_the_guest_wins(tmp_path):
    """A guest already in exact mode is reflected even if the client did not ask."""
    server = FakeQmpServer(tmp_path)
    server.exact = True
    with BudgetMaster(server.path) as master:
        assert master.exact_stop is True
