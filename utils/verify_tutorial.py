#!/usr/bin/env python3
"""Execute the marked book examples, in order, inside a prepared environment."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastdyn.tutorial_examples import inside, suite
from experiment_process import ExperimentProcess


def run_step(root, step, example, logs, timeout):
    log = logs / f"{example.id}.log"
    if "write" in step:
        target = inside(root, step["write"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(example.code)
        log.write_text(f"Saved {step['write']} from {example.source}:{example.line}\n")
    else:
        script = logs / f"{example.id}.sh"
        script.write_text(example.code)
        with log.open("w") as stream:
            process = ExperimentProcess(["bash", "-euo", "pipefail", str(script)],
                                        cwd=root, stdout=stream, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + step.get("timeout_s", timeout)
                while process.poll() is None:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"Timed out; see {log}")
                    time.sleep(.25)
                if process.process.returncode != 0:
                    raise RuntimeError(f"Exit {process.process.returncode}; see {log}")
            finally:
                process.stop()
    text = log.read_text(errors="replace")
    for expected in step.get("contains", []):
        if expected not in text:
            raise AssertionError(f"Missing expected output {expected!r}; see {log}")
    for relative in step.get("artifacts", []):
        path = inside(root, relative)
        if not path.is_file() or not path.stat().st_size:
            raise AssertionError(f"Missing or empty artifact: {relative}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("tests/integration/tutorial.toml"))
    parser.add_argument("--list", action="store_true", help="Validate coverage and list steps without running")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    settings, found = suite(root, args.config)
    for step in settings["steps"]:
        example = found[step["id"]]
        print(f"{example.id}: {example.source}:{example.line}", flush=True)
    if args.list:
        return
    # Examples use the paths printed in the book. Refuse to overwrite an
    # existing experiment; CI and local checks use disposable checkouts.
    outputs = {path for step in settings["steps"] for path in step.get("artifacts", [])}
    outputs.update(step["write"] for step in settings["steps"] if "write" in step)
    if existing := sorted(path for path in outputs if inside(root, path).exists()):
        raise RuntimeError(f"Use a fresh checkout; example outputs already exist: {existing}")
    logs = inside(root, settings["execution"]["output"])
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    for step in settings["steps"]:
        example = found[step["id"]]
        started = time.monotonic()
        result = {"id": example.id, "source": str(example.source), "line": example.line}
        print(f"[tutorial] RUN {example.id}", flush=True)
        try:
            run_step(root, step, example, logs, settings["execution"]["timeout_s"])
            result["status"] = "passed"
        except Exception as error:
            result.update(status="failed", error=str(error))
            raise
        finally:
            result["duration_s"] = round(time.monotonic() - started, 3)
            results.append(result)
            (logs / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"[tutorial] {result['status'].upper()} {example.id} ({result['duration_s']} s)", flush=True)
    print(f"[tutorial] All {len(results)} examples passed", flush=True)


if __name__ == "__main__":
    main()
