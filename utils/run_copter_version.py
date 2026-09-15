#!/usr/bin/env python3
"""Render and run a version-pinned ArduCopter FastDyn configuration."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "configs" / "copter_versions.toml"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_version(requested: str, versions: dict[str, dict[str, str]]) -> str:
    if requested in versions:
        return requested
    matches = [version for version in versions if version.startswith(f"{requested}.")]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(sorted(versions))
    raise SystemExit(f"unknown or ambiguous version {requested!r}; choose one of: {available}")


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(
            f"{name!r} is not on PATH; run this command inside `nix develop`"
        )


def main() -> int:
    manifest = tomllib.loads(MANIFEST.read_text())
    versions = manifest["versions"]

    parser = argparse.ArgumentParser(
        description="Select an exact ArduCopter firmware/config pair and run FastDyn."
    )
    parser.add_argument(
        "--version",
        default=manifest["default"],
        help=f"exact or unambiguous short version (default: {manifest['default']})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="artifact directory (default: out/copter-<version>)",
    )
    parser.add_argument(
        "--overlay",
        action="append",
        type=Path,
        default=[],
        help="fastdyn-config overlay; repeat for multiple overlays",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="render and verify the configuration without starting FastDyn",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list selectable versions and exit",
    )
    parser.add_argument(
        "run_args",
        nargs=argparse.REMAINDER,
        help="arguments after `--` are passed to `fastdyn run`",
    )
    args = parser.parse_args()

    if args.list:
        for version in sorted(versions):
            entry = versions[version]
            marker = " (default)" if version == manifest["default"] else ""
            print(
                f"{version}{marker}: {entry['board']} "
                f"git={entry['git_revision']} config={entry['config']}"
            )
        return 0

    version = resolve_version(args.version, versions)
    entry = versions[version]
    config = ROOT / entry["config"]
    firmware = ROOT / entry["firmware"]
    romfs_defaults = ROOT / entry["romfs_defaults"]
    for path in (config, firmware, romfs_defaults):
        if not path.is_file():
            raise SystemExit(f"required file is missing: {path}")

    actual_hash = sha256(firmware)
    if actual_hash != entry["sha256"]:
        raise SystemExit(
            f"firmware hash mismatch for {version}:\n"
            f"  expected {entry['sha256']}\n"
            f"  actual   {actual_hash}"
        )
    actual_romfs_hash = sha256(romfs_defaults)
    if actual_romfs_hash != entry["romfs_sha256"]:
        raise SystemExit(
            f"ROMFS defaults hash mismatch for {version}:\n"
            f"  expected {entry['romfs_sha256']}\n"
            f"  actual   {actual_romfs_hash}"
        )

    output = args.output or (ROOT / "out" / f"copter-{version.replace('.', '')}")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rendered_config = output / "config.toml"
    work = output / "work"

    require_command("fastdyn-config")
    render_command = [
        "fastdyn-config",
        "--base",
        str(config),
        "--output",
        str(rendered_config),
    ]
    for overlay in args.overlay:
        render_command.extend(["--overlay", str(overlay.resolve())])
    subprocess.run(render_command, cwd=ROOT, check=True)

    print(
        f"selected ArduCopter {version} ({entry['git_revision']}) on "
        f"{entry['board']}; firmware and ROMFS SHA-256 verified"
    )
    print(f"rendered config: {rendered_config}")
    if args.prepare_only:
        return 0

    require_command("fastdyn")
    forwarded = args.run_args[1:] if args.run_args[:1] == ["--"] else args.run_args
    run_command = [
        "fastdyn",
        "run",
        "-c",
        str(rendered_config),
        "-o",
        str(work),
        *forwarded,
    ]
    run_env = os.environ.copy()
    run_env["FASTDYN_ROMFS_DEFAULTS"] = str(romfs_defaults.resolve())
    return subprocess.run(
        run_command, cwd=ROOT, env=run_env, check=False
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
