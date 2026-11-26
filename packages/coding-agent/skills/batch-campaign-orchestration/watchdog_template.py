"""Generic watchdog template for a resumable batch campaign.

A "live" campaign = a running runner process. A "stalled" campaign = no runner
process AND the campaign not complete. The watchdog relaunches the runner
detached and writes a heartbeat log. The runner itself can die freely because
restart is idempotent from the head-watermark.

Adapt the two callables to your campaign:
  - `_runner_cmd(frm, end)`  -> list of argv for the runner
  - `_runner_alive()`        -> list of live PIDs
"""
from __future__ import annotations

import subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEARTBEAT = ROOT / "watchdog_heartbeat.log"


def _log(msg: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    HEARTBEAT.open("a", encoding="utf-8").write(line)
    print(line, end="")


def _runner_cmd(frm: int, end: int) -> list[str]:
    return [sys.executable, str(ROOT / "_campaign_runner.py"), "--from", str(frm), "--end", str(end)]


def _runner_alive() -> list[int]:
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
         "Where-Object { $_.CommandLine -like '*_campaign_runner.py*' } | "
         "Select-Object -ExpandProperty ProcessId"],
        text=True, encoding="utf-8",
    )
    return [int(p) for p in out.split() if p.strip().isdigit()]


def _relaunch(frm: int, end: int) -> int:
    log = open(ROOT / "runner_rel.log", "a", encoding="utf-8")
    log.write(f"\n=== relaunch at {time.strftime('%Y-%m-%d %H:%M:%S')} frm={frm} end={end} ===\n")
    log.flush()
    proc = subprocess.Popen(_runner_cmd(frm, end), cwd=str(ROOT),
                            stdout=log, stderr=subprocess.STDOUT)
    _log(f"relaunched runner {frm}..{end} pid={proc.pid}")
    return proc.pid


def check(frm: int, end: int, is_complete: bool) -> bool:
    if is_complete:
        _log("campaign complete; nothing to do")
        return True
    if _runner_alive():
        _log("runner live")
        return True
    _log("no live runner; relaunching")
    _relaunch(frm, end)
    return False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", type=int, default=1)
    ap.add_argument("--end", type=int, default=3800)
    ap.add_argument("--loop", type=int, default=300)
    ap.add_argument("--complete-flag", type=Path, default=None,
                    help="optional file whose presence marks the campaign complete")
    a = ap.parse_args()
    while True:
        complete = (a.complete_flag is not None and a.complete_flag.exists())
        check(a.frm, a.end, complete)
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
