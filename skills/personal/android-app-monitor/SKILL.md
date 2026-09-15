---
name: android-app-monitor
description: Monitor an Android app through ADB during manual use, recording memory, CPU, frame counters, logs, and operation markers. Use for runtime performance investigations on devices or emulators, including Flutter Android apps.
---

# Android App Monitor

Use the bundled [monitor script](scripts/monitor.py) with Python 3.10+ on macOS or Linux and Android platform-tools (`adb`). No app instrumentation is required.

## Start or Resume

- Infer the package from the project's Android application ID and selected flavor; do not guess from the repository name. Use `adb devices -l` to identify the device. Ask only if multiple targets remain plausible.
- Reuse a monitoring run already identified in the conversation or task record. Inspect existing monitoring processes before starting another collector for the same device/package. Do not stop collectors owned by other sessions.
- Run `python3 <skill-dir>/scripts/monitor.py start --device SERIAL --package APPLICATION_ID`. Optional `--interval 15`, `--duration 1800`, and `--output /absolute/new-directory` control seconds and output location. The default output is a private temporary directory; choose a durable external directory when evidence must survive OS cleanup.
- Keep the returned run directory in the conversation or existing task record. The detached collector performs sampling without model polling. Default duration is 30 minutes; use the requested duration for longer investigations.
- Tell the user collection has started, its deadline, and the run directory. Let them exercise the app. Do not operate wallets, submit transactions, or navigate the app merely because monitoring was requested.

## Control

Use the same script with:

```text
status RUN_DIRECTORY
mark RUN_DIRECTORY "Opened wallet"
stop RUN_DIRECTORY
report RUN_DIRECTORY
```

`mark` timestamps a user-reported or directly observed operation. Never invent operation times retroactively. `stop` requests shutdown; use `status` to confirm completion. A blocked ADB call can take up to 10 seconds per call to return. Stopping preserves evidence and does not stop the app or ADB server. Reports can also be read during collection and are then partial.

## Interpret Evidence

- `report` emits JSON with configuration, lifecycle status, first/last/peak memory, CPU summaries, PID changes, incomplete samples, markers, and artifact paths. Begin with that summary; inspect only relevant timestamp ranges in `snapshots.jsonl`, `samples.jsonl`, and `logcat.txt`.
- Compare baseline, the marked operation, and recovery after returning to the same screen. Preserve missing values as unknown. App absence and ADB failures are recorded, not interpreted as zero memory.
- Memory values are KiB; CPU is Android `dumpsys cpuinfo`'s recent-window percentage, not an instantaneous sample. Frame counters are cumulative since Android's last reset and cannot be compared across process restarts as one interval.
- Collection follows the exact package's main PID, restarting PID-filtered logcat when the PID changes. Secondary processes are outside this first version. Logcat covers app-process messages, including crashes emitted there; system-process ANR messages require a separate targeted system log investigation.
- Android `gfxinfo` may omit Flutter-rendered frames. Missing frame counters do not establish smoothness; use Flutter profiling/Perfetto when frame timing is the question. Increasing PSS/RSS alone does not prove a leak; repeat the operation and investigate heap retention when warranted.
- State device, build/flavor/mode, period, observed changes, and relevant markers in conclusions. The run saves package metadata and device properties; determine Flutter debug/profile/release mode separately when it matters. Emulator and debug-build measurements are not production performance claims.
- Logs may contain user data. Keep them local; quote minimal redacted excerpts. Collecting does not authorize uploading artifacts or modifying the app. Do not claim the monitor captures every allocation or proves absence of leaks.
