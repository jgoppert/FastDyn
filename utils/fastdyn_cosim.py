#!/usr/bin/env python3
"""Drive a FastDyn/QEMU instance as a co-simulation slave.

The patched QEMU exposes a virtual-time budget over QMP. A master grants the
guest a slice of virtual time with ``run-for``; the guest runs exactly that
far and then pauses itself, so an external algorithm — an FMI master, a
network simulator, or another FastDyn instance — owns the clock.

The protocol is asynchronous:

    run-for(dt)   ->  RESUME event, immediate reply, guest runs
                  ->  STOP event when the deadline is reached
    query-budget  ->  the virtual time the guest actually reached

Because the guest halts at a translation-block boundary at or after the
deadline, a slice may overshoot slightly. Reconcile against the reported time
rather than assuming the granted amount was consumed exactly.

A run configured with ``[Machine] exact_budget_stop = true`` halts precisely on
every deadline instead. ``exact_stop=True`` here selects the same mode at
runtime, for a harness that drives both.

Use as a library::

    with BudgetMaster("/tmp/fastdyn.qmp") as master:
        while master.time_ns < end_ns:
            master.run_slice(1_000_000)

or from the command line::

    utils/fastdyn_cosim.py /tmp/fastdyn.qmp --slice-ms 20 --slices 5
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time


class CosimError(RuntimeError):
    """The slave could not be driven as requested."""


class BudgetMaster:
    """A QMP client that owns a FastDyn guest's virtual clock."""

    def __init__(self, qmp_socket: str, connect_timeout: float = 15.0,
                 slice_timeout: float = 60.0, exact_stop: bool = False):
        self.qmp_socket = qmp_socket
        self.connect_timeout = connect_timeout
        self.slice_timeout = slice_timeout
        self.exact_stop = exact_stop
        self._socket: socket.socket | None = None
        self._stream = None
        self._pending_events: list[dict] = []
        self.time_ns = 0
        self.budget_ns = 0

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "BudgetMaster":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self) -> None:
        """Wait for the socket to appear, then negotiate QMP capabilities."""
        deadline = time.monotonic() + self.connect_timeout
        while True:
            try:
                self._socket = socket.socket(socket.AF_UNIX)
                self._socket.connect(self.qmp_socket)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() > deadline:
                    raise CosimError(
                        f"no QMP socket at {self.qmp_socket} after "
                        f"{self.connect_timeout:g}s; is the guest running with "
                        "qmp_socket set and stop_on_start = true?"
                    ) from None
                time.sleep(0.05)
        self._stream = self._socket.makefile("rw")
        self._read_message()  # QMP greeting
        self._command("qmp_capabilities")
        if self.exact_stop:
            self._command("set-budget-mode", exact=True)
        self._refresh()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    # -- QMP plumbing ------------------------------------------------------

    def _read_message(self) -> dict:
        line = self._stream.readline()
        if not line:
            raise CosimError("QMP connection closed by the guest")
        return json.loads(line)

    def _command(self, name: str, **arguments) -> dict:
        """Issue one command, buffering any events that arrive first."""
        request = {"execute": name}
        if arguments:
            request["arguments"] = arguments
        self._stream.write(json.dumps(request) + "\n")
        self._stream.flush()
        while True:
            message = self._read_message()
            if "event" in message:
                self._pending_events.append(message)
                continue
            if "error" in message:
                raise CosimError(f"{name} failed: {message['error']}")
            return message.get("return", {})

    def _wait_for_event(self, name: str, timeout: float) -> dict:
        for index, event in enumerate(self._pending_events):
            if event["event"] == name:
                return self._pending_events.pop(index)
        self._socket.settimeout(timeout)
        try:
            while True:
                message = self._read_message()
                if message.get("event") == name:
                    return message
                if message.get("event") == "SHUTDOWN":
                    raise CosimError(
                        "guest shut down before its budget was exhausted; "
                        "the firmware finished inside this slice"
                    )
                if "event" in message:
                    self._pending_events.append(message)
        except socket.timeout:
            raise CosimError(
                f"no {name} event within {timeout:g}s; the guest may be "
                "blocked, or the slice may be larger than it can execute"
            ) from None
        finally:
            self._socket.settimeout(None)

    # -- budget interface --------------------------------------------------

    def _refresh(self) -> None:
        state = self._command("query-budget")
        self.budget_ns = int(state["totalbudget"])
        self.time_ns = int(state["currenttime"])
        self.exact_stop = bool(state.get("exact", self.exact_stop))

    @property
    def status(self) -> str:
        return self._command("query-status")["status"]

    def grant(self, duration_ns: int) -> int:
        """Grant `duration_ns` of virtual time without waiting for the guest.

        `run-for` is asynchronous, so this returns while the guest is still
        running. Pair it with wait_for_slice(). Granting to several guests
        before waiting on any of them is how a master keeps them in lockstep.

        Returns the new cumulative deadline.
        """
        if duration_ns <= 0:
            raise ValueError("slice duration must be positive")
        result = self._command("run-for", budget=duration_ns)
        return int(result["totalbudget"])

    def wait_for_slice(self) -> int:
        """Block until the guest halts, then report the virtual time reached."""
        self._wait_for_event("STOP", self.slice_timeout)
        self._refresh()
        return self.time_ns

    def run_slice(self, duration_ns: int) -> int:
        """Grant `duration_ns` of virtual time and block until the guest stops.

        Returns the virtual time actually reached. With exact-stop mode off
        this may exceed the deadline by less than one translation block; with
        it on the two are equal.
        """
        self.grant(duration_ns)
        return self.wait_for_slice()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drive a FastDyn guest as a co-simulation slave.")
    parser.add_argument("qmp_socket", help="path to the guest's QMP unix socket")
    parser.add_argument("--slice-ms", type=float, default=20.0,
                        help="virtual milliseconds per slice (default: 20)")
    parser.add_argument("--slices", type=int, default=5,
                        help="number of slices to grant (default: 5)")
    parser.add_argument("--slice-timeout", type=float, default=60.0,
                        help="seconds to wait for each slice to complete")
    parser.add_argument("--exact", action="store_true",
                        help="halt precisely on each deadline instead of at the "
                             "first block boundary after it")
    args = parser.parse_args()

    slice_ns = int(args.slice_ms * 1_000_000)
    try:
        with BudgetMaster(args.qmp_socket, slice_timeout=args.slice_timeout,
                          exact_stop=args.exact) as master:
            print(f"connected; guest is {master.status} at {master.time_ns} ns"
                  f"{' (exact-stop mode)' if master.exact_stop else ''}")
            print(f"{'slice':>6} {'granted(ns)':>13} {'advanced(ns)':>14} "
                  f"{'reached(ns)':>13} {'overshoot':>10} {'status':>8}")
            for index in range(args.slices):
                before = master.time_ns
                reached = master.run_slice(slice_ns)
                print(f"{index:>6} {slice_ns:>13} {reached - before:>14} "
                      f"{reached:>13} {reached - master.budget_ns:>10} "
                      f"{master.status:>8}")
    except CosimError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
