#!/usr/bin/env python3
"""Bounded, detached ADB sampling using only the Python standard library."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def append(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=True) + "\n")


def adb(config, *args, allow_empty=False):
    result = subprocess.run(
        [config["adb"], "-s", config["device"], *args],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode and not (
        allow_empty and result.returncode == 1 and not result.stderr.strip()
    ):
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ADB failed")
    return result.stdout


def number(pattern, text):
    match = re.search(pattern, text, re.MULTILINE)
    return float(match[1]) if match else None


def metrics(memory, cpu, frames, package):
    # Summary labels avoid guessing TOTAL column positions across Android versions.
    result = {
        "pss_kib": number(r"TOTAL PSS:\s*(\d+)", memory),
        "rss_kib": number(r"TOTAL RSS:\s*(\d+)", memory),
        "swap_pss_kib": number(r"TOTAL SWAP PSS:\s*(\d+)", memory),
        "native_pss_kib": number(r"^\s*Native Heap\s+(\d+)", memory),
        "dalvik_pss_kib": number(r"^\s*Dalvik Heap\s+(\d+)", memory),
        "graphics_pss_kib": number(r"^\s*Graphics:\s*(\d+)", memory),
        "cpu_pct": number(r"^\s*([\d.]+)%\s+\d+/" + re.escape(package) + r":\s", cpu),
        "frames": number(r"Total frames rendered:\s*(\d+)", frames),
        "janky_frames": number(r"Janky frames:\s*(\d+)", frames),
    }
    if result["pss_kib"] is None:
        result["pss_kib"] = number(r"^\s*TOTAL\s+(\d+)\s", memory)
    return result


def status(directory):
    state = json.loads((directory / "state.json").read_text())
    with (directory / "collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            state["collector_active"] = True
        else:
            state["collector_active"] = False
            if state["status"] in {"running", "starting"}:
                state["status"] = "interrupted"
    return state


def stop_child(child):
    if child is not None:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def collect(directory):
    config = json.loads((directory / "run.json").read_text())
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    with (directory / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = {"status": "running", "started_at": now(), "pid": os.getpid()}
        save(directory / "state.json", state)
        deadline = time.monotonic() + config["duration"]
        child, log_pid = None, None
        try:
            with (directory / "logcat.txt").open("a") as logs:
                while not done.is_set() and not (directory / "stop").exists() and time.monotonic() < deadline:
                    row = {"timestamp": now(), "pid": None, "errors": []}
                    raw = {"timestamp": row["timestamp"]}
                    try:
                        pid_text = adb(config, "shell", "pidof", config["package"], allow_empty=True).strip()
                        if pid_text and not re.fullmatch(r"\d+", pid_text):
                            raise RuntimeError("Expected one main process PID: " + pid_text)
                        row["pid"] = pid_text or None
                        if pid_text != log_pid or (child is not None and child.poll() is not None):
                            if child is not None and child.poll() is not None:
                                row["errors"].append("logcat exited; reconnecting")
                            stop_child(child)
                            child, log_pid = None, pid_text
                            if pid_text:
                                child = subprocess.Popen(
                                    [config["adb"], "-s", config["device"], "logcat", "--pid=" + pid_text, "-T", "1", "-v", "threadtime"],
                                    stdin=subprocess.DEVNULL, stdout=logs, stderr=logs,
                                )
                        if pid_text:
                            for name, args in {
                                "memory": ("shell", "dumpsys", "meminfo", "-d", pid_text),
                                "cpu": ("shell", "dumpsys", "cpuinfo"),
                                "frames": ("shell", "dumpsys", "gfxinfo", config["package"]),
                                "threads": ("shell", "cat", "/proc/" + pid_text + "/status"),
                            }.items():
                                if done.is_set() or (directory / "stop").exists():
                                    break
                                try:
                                    raw[name] = adb(config, *args)
                                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                                    row["errors"].append(name + ": " + str(exc))
                            row.update(metrics(raw.get("memory", ""), raw.get("cpu", ""), raw.get("frames", ""), config["package"]))
                            row["threads"] = number(r"^Threads:\s*(\d+)", raw.get("threads", ""))
                            row["status"] = "sampled" if row["pss_kib"] is not None else "unavailable"
                        else:
                            row["status"] = "app_not_running"
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        row["status"] = "unavailable"
                        row["errors"].append(str(exc))
                    append(directory / "snapshots.jsonl", raw)
                    append(directory / "samples.jsonl", row)
                    until = min(deadline, time.monotonic() + config["interval"])
                    while time.monotonic() < until and not (directory / "stop").exists():
                        if done.wait(min(0.2, max(0, until - time.monotonic()))):
                            break
            state["status"] = "stopped" if done.is_set() or (directory / "stop").exists() else "completed"
        except Exception as exc:
            state.update(status="failed", error=str(exc))
        finally:
            stop_child(child)
            state["ended_at"] = now()
            save(directory / "state.json", state)


def start(args):
    binary = shutil.which("adb")
    if binary is None:
        raise ValueError("adb is not on PATH; install Android platform-tools")
    config = {"adb": binary, "device": args.device, "package": args.package,
              "interval": args.interval, "duration": args.duration}
    adb(config, "get-state")
    package = adb(config, "shell", "pm", "path", args.package)
    if not package.startswith("package:"):
        raise ValueError("Package is not installed on the selected device")
    metadata = {"package": adb(config, "shell", "dumpsys", "package", args.package),
                "device": adb(config, "shell", "getprop")}
    if args.output:
        directory = Path(args.output).expanduser().resolve()
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    else:
        directory = Path(tempfile.mkdtemp(prefix="android-app-monitor-"))
    save(directory / "run.json", config)
    save(directory / "metadata.json", metadata)
    save(directory / "state.json", {"status": "starting"})
    with (directory / "collector.log").open("a") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_collect", str(directory)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    for _ in range(100):
        if json.loads((directory / "state.json").read_text())["status"] != "starting":
            return {"directory": str(directory), "duration_seconds": args.duration, **status(directory)}
        if child.poll() is not None:
            raise RuntimeError("Collector failed to start; see " + str(directory / "collector.log"))
        time.sleep(0.05)
    (directory / "stop").touch()
    raise RuntimeError("Collector startup timed out; stop requested in " + str(directory))


def rows(path):
    if not path.exists():
        return []
    with path.open() as stream:
        # A live writer may have appended only part of the last record.
        return [json.loads(line) for line in stream if line.endswith("\n")]


def report(directory):
    samples = rows(directory / "samples.jsonl")
    result = {"directory": str(directory), "run": json.loads((directory / "run.json").read_text()),
              "state": status(directory), "samples": len(samples),
              "incomplete_samples": sum(row["status"] != "sampled" or bool(row["errors"]) for row in samples),
              "markers": rows(directory / "markers.jsonl"), "metrics": {}, "pid_transitions": []}
    previous = None
    for row in samples:
        if row["pid"] != previous:
            result["pid_transitions"].append({"timestamp": row["timestamp"], "pid": row["pid"]})
            previous = row["pid"]
    for key in ("pss_kib", "rss_kib", "swap_pss_kib", "native_pss_kib", "dalvik_pss_kib", "graphics_pss_kib", "cpu_pct", "threads"):
        valid = [row for row in samples if row.get(key) is not None]
        if valid:
            peak = max(valid, key=lambda row: row[key])
            result["metrics"][key] = {"first": valid[0][key], "last": valid[-1][key],
                                      "peak": peak[key], "peak_at": peak["timestamp"], "available_samples": len(valid)}
    result["artifacts"] = {name: str(directory / name) for name in ("samples.jsonl", "snapshots.jsonl", "logcat.txt", "metadata.json", "collector.log")}
    return result


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return value


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("start")
    begin.add_argument("--device", required=True)
    begin.add_argument("--package", required=True)
    begin.add_argument("--interval", type=positive, default=15)
    begin.add_argument("--duration", type=positive, default=1800)
    begin.add_argument("--output")
    for command in ("status", "stop", "mark", "report", "_collect"):
        sub = commands.add_parser(command)
        sub.add_argument("directory", type=Path)
        if command == "mark":
            sub.add_argument("label")
    args = parser.parse_args()
    try:
        if args.command == "start":
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", args.package):
                raise ValueError("Invalid Android application ID")
            if not args.device or args.device.startswith("-") or any(c.isspace() for c in args.device):
                raise ValueError("Invalid device serial")
            result = start(args)
        elif args.command == "_collect":
            collect(args.directory)
            return 0
        elif args.command == "report":
            result = report(args.directory)
        else:
            result = status(args.directory)
            if args.command == "stop" and result["collector_active"]:
                (args.directory / "stop").touch()
                result["stop_requested"] = True
            elif args.command == "mark":
                if not result["collector_active"]:
                    raise ValueError("Collector is not running")
                if not args.label.strip():
                    raise ValueError("Marker cannot be empty")
                result = {"timestamp": now(), "label": args.label}
                append(args.directory / "markers.jsonl", result)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
