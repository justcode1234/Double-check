"""Open the local Moomoo competition dashboard and arm approved paper startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "moomoo-dual-strategy"
RUNTIME = PROJECT / "private" / "launcher"
PORTS = range(8876, 8886)
STARTUP_LABEL = "com.portbrain.moomoo-competition-start"


def project_python():
    relative = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    executable = PROJECT / relative
    if not executable.is_file():
        raise RuntimeError(f"Project Python is missing at {executable}. See SETUP_AND_RUN.md in the project folder.")
    return executable


@contextmanager
def startup_lock():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / "startup.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            def lock():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def lock():
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(handle, fcntl.LOCK_UN)

        deadline = time.monotonic() + 25
        while True:
            try:
                lock()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Startup did not finish within 25 seconds") from None
                time.sleep(0.15)
        try:
            yield
        finally:
            unlock()


def is_dashboard(port):
    token = hashlib.sha256(os.fsencode(os.path.normcase(str(PROJECT.resolve())))).hexdigest()
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/launcher", timeout=0.4) as response:
            info = json.load(response)
    except (URLError, OSError, ValueError):
        return False
    return info == {"service": "moomoo-competition-dashboard", "project": token, "live_votes": True}


def available_port(port):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def dashboard():
    for port in PORTS:
        if is_dashboard(port):
            return port

    for port in PORTS:
        if not available_port(port):
            continue
        detached = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                    if os.name == "nt" else {"start_new_session": True})
        with (RUNTIME / "dashboard.log").open("a", encoding="utf-8") as log:
            child = subprocess.Popen(
                [str(project_python()), "-m", "dual_strategy.cli", "serve", "--live-votes", "--port", str(port)],
                cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}, **detached,
            )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if is_dashboard(port):
                return port
            if child.poll() is not None:
                break
            time.sleep(0.15)
        if child.poll() is None:
            raise RuntimeError(f"The dashboard did not become ready. See {RUNTIME / 'dashboard.log'}.")
        if available_port(port):
            raise RuntimeError(f"The dashboard exited during startup. See {RUNTIME / 'dashboard.log'}.")
    raise RuntimeError("No local dashboard port is available")


def arm_paper_scheduler():
    if sys.platform != "darwin":
        return "Paper trading startup is handled by the authorized Mac scheduler."
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{STARTUP_LABEL}"
    launchctl = "/bin/launchctl"
    active = subprocess.run([launchctl, "print", service], capture_output=True, timeout=5)
    if active.returncode == 0:
        return None
    account = PROJECT / "private" / "competition"
    if not (account / "account.json").is_file() or not (account / "live" / "state.json").is_file():
        return "Paper startup needs the confirmed competition account and its saved state on this Mac."
    scheduler = PROJECT / "scripts" / "schedule_competition.py"
    config = subprocess.run([str(project_python()), str(scheduler), "--plist"], cwd=PROJECT,
                            check=True, capture_output=True, timeout=15)
    (account / "scheduler").mkdir(mode=0o700, parents=True, exist_ok=True)
    plist = RUNTIME / f"{STARTUP_LABEL}.plist"
    plist.write_bytes(config.stdout)
    started = subprocess.run([launchctl, "bootstrap", domain, str(plist)], capture_output=True, timeout=10)
    if started.returncode and subprocess.run([launchctl, "print", service], capture_output=True, timeout=5).returncode:
        raise RuntimeError("macOS launchd could not register the paper scheduler")
    return "The paper scheduler will run its preflight in the authorized start window."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-only", action="store_true", help="Open the dashboard without arming the scheduler")
    parser.add_argument("--no-browser", action="store_true", help="Print the dashboard URL without opening a tab")
    args = parser.parse_args(argv)
    try:
        with startup_lock():
            port = dashboard()
    except (OSError, RuntimeError) as exc:
        print(f"Dashboard startup failed: {exc}", file=sys.stderr, flush=True)
        return 1

    url = f"http://127.0.0.1:{port}/"
    print(url, flush=True)
    if not args.no_browser:
        webbrowser.open(url, new=2)
    if not args.dashboard_only:
        try:
            with startup_lock():
                note = arm_paper_scheduler()
            if note:
                print(note, flush=True)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"Paper scheduler startup failed: {exc}", file=sys.stderr, flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
