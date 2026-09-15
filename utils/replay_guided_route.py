#!/usr/bin/env python3
"""Replay the log-3 geographic route with ArduCopter GUIDED setpoints.

This is intentionally labelled a route replay rather than an AUTO mission
replay: it exercises the exact firmware navigation/control loops at the logged
coordinates while avoiding the mission-upload service in rehosted Copter 4.7.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
import time

from pymavlink import mavutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "virtuals/physics/flight_controllers/courbet/mavlink"))
import mav_command_and_control as mission  # noqa: E402


ROUTE = [
    {"name": "WP1_LOITER_15", "lat": 39.4292654, "lon": -76.2037146, "alt": 15.0,
     "hold_s": 15.0, "timeout_s": 35.0},
    {"name": "WP2_LOITER_15", "lat": 39.4277985, "lon": -76.2031138, "alt": 15.0,
     "hold_s": 15.0, "timeout_s": 60.0},
    {"name": "WP3_LOITER_15", "lat": 39.4286687, "lon": -76.2030816, "alt": 15.0,
     "hold_s": 15.0, "timeout_s": 50.0},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("param_file", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("summary_json", type=Path)
    parser.add_argument("--connect", default="udpin:127.0.0.1:14552")
    parser.add_argument("--ready-timeout", type=float, default=150.0)
    parser.add_argument("--flight-timeout", type=float, default=240.0)
    parser.add_argument("--payload-kg", type=float, default=0.0)
    return parser.parse_args()


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    north = math.radians(lat2 - lat1) * 6378137.0
    east = math.radians(lon2 - lon1) * 6378137.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(north, east)


def send_global_target(mav: mavutil.mavfile, target: dict[str, float], boot_ms: int) -> None:
    ignore_velocity_accel_yaw = sum(1 << bit for bit in (3, 4, 5, 6, 7, 8, 10, 11))
    mav.mav.set_position_target_global_int_send(
        boot_ms,
        mav.target_system,
        mav.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        ignore_velocity_accel_yaw,
        int(round(target["lat"] * 1e7)),
        int(round(target["lon"] * 1e7)),
        target["alt"],
        0, 0, 0, 0, 0, 0, 0, 0,
    )


def main() -> int:
    args = parse_args()
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    mav = mission.connect(args.connect, 360)
    rows: list[dict[str, float | str]] = []
    reached: list[dict[str, float | str]] = []
    current = {"lat": math.nan, "lon": math.nan, "alt": math.nan, "boot_ms": 0}
    attitude = {"roll": math.nan, "pitch": math.nan, "yaw": math.nan}
    pwm = [math.nan] * 4
    ekf = {
        "flags": 0,
        "velocity_variance": math.nan,
        "pos_horiz_variance": math.nan,
        "pos_vert_variance": math.nan,
        "compass_variance": math.nan,
        "terrain_alt_variance": math.nan,
    }
    status_texts: list[str] = []
    mode = "UNKNOWN"
    armed = False
    started_wall = time.monotonic()

    def handle(msg: object, phase: str) -> None:
        nonlocal armed, mode
        kind = msg.get_type()
        if kind == "GLOBAL_POSITION_INT":
            current.update(lat=msg.lat / 1e7, lon=msg.lon / 1e7,
                           alt=msg.relative_alt / 1000.0, boot_ms=msg.time_boot_ms)
            rows.append({
                "time_boot_s": msg.time_boot_ms / 1000.0,
                "wall_elapsed_s": time.monotonic() - started_wall,
                "phase": phase,
                "lat": current["lat"], "lon": current["lon"],
                "relative_alt_m": current["alt"],
                "roll_deg": attitude["roll"], "pitch_deg": attitude["pitch"],
                "yaw_deg": attitude["yaw"],
                "mode": mode,
                "armed": int(armed),
                "ekf_flags": ekf["flags"],
                "ekf_velocity_variance": ekf["velocity_variance"],
                "ekf_pos_horiz_variance": ekf["pos_horiz_variance"],
                "ekf_pos_vert_variance": ekf["pos_vert_variance"],
                "ekf_compass_variance": ekf["compass_variance"],
                "ekf_terrain_alt_variance": ekf["terrain_alt_variance"],
                **{f"pwm{i + 1}": pwm[i] for i in range(4)},
            })
        elif kind == "ATTITUDE":
            attitude.update(roll=math.degrees(msg.roll), pitch=math.degrees(msg.pitch),
                            yaw=math.degrees(msg.yaw))
        elif kind == "SERVO_OUTPUT_RAW":
            for i in range(4):
                pwm[i] = getattr(msg, f"servo{i + 1}_raw")
        elif kind == "HEARTBEAT":
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            mode = mavutil.mode_string_v10(msg)
        elif kind == "EKF_STATUS_REPORT":
            for name in ekf:
                ekf[name] = int(msg.flags) if name == "flags" else float(getattr(msg, name))
        elif kind == "STATUSTEXT":
            text = str(msg.text)
            status_texts.append(text)
            print(f"[route] {text}", flush=True)

    try:
        mission.load_params(mav, args.param_file)
        mission.wait_for_autopilot_ready(mav, args.ready_timeout)
        mission.wait_for_mode(mav, "STABILIZE", 0, 30)

        arm_deadline = time.monotonic() + 45
        next_arm = 0.0
        while time.monotonic() < arm_deadline and not armed:
            now = time.monotonic()
            if now >= next_arm:
                mav.mav.command_long_send(
                    mav.target_system, mav.target_component,
                    mavutil.mavlink.MAV_CMD_DO_SET_SAFETY_SWITCH_STATE,
                    0, 1, 0, 0, 0, 0, 0, 0,
                )
                mission.request_arm(mav, force=True)
                next_arm = now + 4
            msg = mav.recv_match(blocking=True, timeout=1)
            if msg is not None:
                handle(msg, "arming")
        if not armed:
            raise TimeoutError("ArduCopter did not arm")

        mission.wait_for_gps_fusion(mav, 0, 60)
        mission.wait_for_mode(mav, "GUIDED", 0, 45)
        for message_id, hz in ((33, 10), (30, 20), (36, 20), (193, 5)):
            mav.mav.command_long_send(
                mav.target_system, mav.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                message_id, 1e6 / hz, 0, 0, 0, 0, 0,
            )
        mav.mav.command_long_send(
            mav.target_system, mav.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0,
            0, 0, 0, 0, 0, 0, 15.0,
        )

        flight_deadline = time.monotonic() + args.flight_timeout
        while time.monotonic() < flight_deadline and current["alt"] < 14.0:
            msg = mav.recv_match(blocking=True, timeout=1)
            if msg is not None:
                handle(msg, "takeoff")
        if current["alt"] < 14.0:
            raise TimeoutError("takeoff did not reach 14 m")

        for target in ROUTE:
            phase = str(target["name"])
            target_deadline = min(flight_deadline, time.monotonic() + target["timeout_s"])
            next_send = 0.0
            arrival_wall: float | None = None
            while time.monotonic() < target_deadline:
                now = time.monotonic()
                if now >= next_send:
                    send_global_target(mav, target, int(current["boot_ms"]))
                    next_send = now + 0.5
                msg = mav.recv_match(blocking=True, timeout=0.25)
                if msg is not None:
                    handle(msg, phase)
                if math.isfinite(current["lat"]):
                    horizontal = distance_m(current["lat"], current["lon"],
                                            target["lat"], target["lon"])
                    if horizontal <= 2.0 and abs(current["alt"] - target["alt"]) <= 1.0:
                        if arrival_wall is None:
                            arrival_wall = now
                            reached.append({"name": phase,
                                            "time_boot_s": current["boot_ms"] / 1000.0,
                                            "horizontal_error_m": horizontal,
                                            "altitude_error_m": current["alt"] - target["alt"]})
                            print(f"[route] reached {phase}: horizontal={horizontal:.2f}m", flush=True)
                        if now - arrival_wall >= target["hold_s"]:
                            break
            else:
                raise TimeoutError(f"did not reach {phase}")

        mission.set_mode(mav, "RTL")
        landing_deadline = min(flight_deadline, time.monotonic() + 100)
        while time.monotonic() < landing_deadline:
            msg = mav.recv_match(blocking=True, timeout=1)
            if msg is not None:
                handle(msg, "RTL")
            if current["alt"] <= 0.5 and not armed:
                break
        if current["alt"] > 0.5 or armed:
            raise TimeoutError("RTL did not land and disarm")

        result = {"passed": True, "replay_kind": "GUIDED geographic route",
                  "firmware": "ArduCopter 4.7.0 (1511f271)", "reached": reached,
                  "payload_kg": args.payload_kg, "samples": len(rows),
                  "status_texts": status_texts}
    except Exception as exc:
        result = {"passed": False, "replay_kind": "GUIDED geographic route",
                  "firmware": "ArduCopter 4.7.0 (1511f271)", "reached": reached,
                  "payload_kg": args.payload_kg, "samples": len(rows),
                  "status_texts": status_texts,
                  "error": f"{type(exc).__name__}: {exc}"}
        raise
    finally:
        if rows:
            with args.output_csv.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        args.summary_json.write_text(json.dumps(result, indent=2) + "\n")
        mav.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
