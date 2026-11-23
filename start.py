"""Open the local Moomoo competition dashboard."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "moomoo-dual-strategy"
RUNTIME = PROJECT / "private" / "launcher"
PORTS = range(8876, 8886)
SYSTEMD_UNIT = "moomoo-competition-start"
DASHBOARD_UNIT = "moomoo-dashboard.service"
STARTUP_TIMEOUT = 300


def project_python():
    relative = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    return PROJECT / relative


def prepare_project_python():
    python = project_python()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    ready = [str(python), "-c", "import dual_strategy.cli, exchange_calendars, pandas, yaml"]
    if python.is_file() and subprocess.run(ready, cwd=PROJECT, capture_output=True, timeout=20,
                                           creationflags=flags).returncode == 0:
        return python
    if sys.version_info < (3, 12):
        raise RuntimeError("First-time dashboard setup requires Python 3.12 or later.")

    log_path = RUNTIME / "setup.log"
    report_message(f"Preparing the project Python environment. Setup output: {log_path}")
    deadline = time.monotonic() + STARTUP_TIMEOUT
    commands = [
        [sys.executable, "-m", "venv", str(PROJECT / ".venv")],
        [str(python), "-m", "pip", "install", "-r", "requirements.lock"],
        [str(python), "-m", "pip", "install", "--no-deps", "-e", "."],
        ready,
    ]
    with log_path.open("w", encoding="utf-8") as log:
        try:
            for command in commands:
                subprocess.run(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, check=True, creationflags=flags,
                               timeout=max(0, deadline - time.monotonic()))
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Python environment setup failed. See {log_path} and the repository README.md.") from exc
    return python


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

        deadline = time.monotonic() + STARTUP_TIMEOUT + 15
        while True:
            try:
                lock()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another dashboard launch is still preparing the environment.") from None
                time.sleep(0.15)
        try:
            yield
        finally:
            unlock()


def is_dashboard(port):
    token = hashlib.sha256(os.fsencode(os.path.normcase(str(PROJECT.resolve())))).hexdigest()
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/launcher", timeout=0.4) as response:
            info = json.load(response)
    except (URLError, OSError, ValueError):
        return False
    return info == {"service": "moomoo-competition-dashboard", "project": token, "live_votes": True}


def available_port(port):
    with socket.socket() as sock:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def dashboard():
    for port in PORTS:
        if is_dashboard(port):
            return port

    if linux_dashboard_enabled():
        return wait_for_background_dashboard()

    python = prepare_project_python()
    for port in PORTS:
        if not available_port(port):
            continue
        detached = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                    if os.name == "nt" else {"start_new_session": True})
        with (RUNTIME / "dashboard.log").open("a", encoding="utf-8") as log:
            child = subprocess.Popen(
                [str(python), "-m", "dual_strategy.cli", "serve", "--live-votes", "--port", str(port)],
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


def install_windows_shortcut():
    if os.name != "nt":
        raise RuntimeError("The desktop shortcut is only available on Windows.")
    pythonw = prepare_project_python().with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError("pythonw.exe is missing from the project environment.")
    script = """
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (!$desktop) { throw 'Could not find the Windows desktop folder.' }
$path = Join-Path $desktop 'Moo Moo Dashboard.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$arguments = '"' + $env:MOOMOO_START_SCRIPT + '"'
if ((Test-Path -LiteralPath $path) -and
    ($link.TargetPath -ne $env:MOOMOO_START_PYTHONW -or $link.Arguments -ne $arguments)) {
    throw 'A different Moo Moo Dashboard shortcut already exists.'
}
$link.TargetPath = $env:MOOMOO_START_PYTHONW
$link.Arguments = $arguments
$link.WorkingDirectory = $env:MOOMOO_START_ROOT
$link.Save()
"""
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
                   env={**os.environ, "MOOMOO_START_PYTHONW": str(pythonw),
                        "MOOMOO_START_SCRIPT": str(ROOT / "start.py"), "MOOMOO_START_ROOT": str(ROOT)})
    return "Moo Moo Dashboard shortcut installed on the Windows desktop."


def systemd_argument(path):
    value = str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{value}"'


def systemd_units(python, scheduler):
    return {
        f"{SYSTEMD_UNIT}.service": (
            "[Unit]\nDescription=Moomoo competition paper scheduler\n\n"
            "[Service]\nType=exec\nUMask=0077\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n"
            f"ExecStart=:{systemd_argument(python)} {systemd_argument(scheduler)} --run\n"
        ),
        f"{SYSTEMD_UNIT}.timer": (
            "[Unit]\nDescription=Check Moomoo competition paper startup window\n\n"
            "[Timer]\nOnActiveSec=1s\nOnUnitInactiveSec=60s\nAccuracySec=1s\n\n"
            "[Install]\nWantedBy=timers.target\n"
        ),
    }


def systemd_user_directory():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user"


def enable_systemd_units(units, target, purpose):
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise RuntimeError("This option requires a systemd user manager.")
    command = [systemctl, "--user"]
    available = subprocess.run([*command, "show-environment"], capture_output=True, timeout=10)
    if available.returncode:
        raise RuntimeError("The systemd user manager is unavailable. See the repository README.md.")
    root = systemd_user_directory()
    for name, content in units.items():
        path = root / name
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise RuntimeError(f"A different {purpose} unit already exists at {path}.")
    root.mkdir(parents=True, exist_ok=True)
    for name, content in units.items():
        path = root / name
        if not path.exists():
            with path.open("x", encoding="utf-8") as file:
                file.write(content)
    subprocess.run([*command, "daemon-reload"], check=True, timeout=10)
    subprocess.run([*command, "enable", "--now", target], check=True, timeout=15)


def arm_linux_scheduler():
    units = systemd_units(project_python(), PROJECT / "scripts/schedule_competition.py")
    enable_systemd_units(units, f"{SYSTEMD_UNIT}.timer", "scheduler")
    return "The Linux timer will run the paper scheduler's preflight in its authorized startup window."


def dashboard_service(python):
    return (
        "[Unit]\nDescription=Moo-Moo dashboard and signal monitor\n\n"
        "[Service]\nType=exec\nUMask=0077\nRestart=always\nRestartSec=5s\nKillSignal=SIGINT\n"
        "Environment=PYTHONDONTWRITEBYTECODE=1\nEnvironment=PYTHONUNBUFFERED=1\n"
        f"ExecStart=:{systemd_argument(python)} -m dual_strategy.cli --project {systemd_argument(PROJECT)} "
        f"serve --live-votes --port {PORTS.start}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def linux_dashboard_enabled():
    if not sys.platform.startswith("linux") or not (systemd_user_directory() / DASHBOARD_UNIT).is_file():
        return False
    systemctl = shutil.which("systemctl")
    return bool(systemctl and subprocess.run([systemctl, "--user", "is-enabled", "--quiet", DASHBOARD_UNIT],
                                             timeout=5).returncode == 0)


def wait_for_background_dashboard():
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if is_dashboard(PORTS.start):
            return PORTS.start
        time.sleep(0.15)
    raise RuntimeError(f"The background dashboard did not become ready. See journalctl --user -u {DASHBOARD_UNIT}.")


def enable_linux_dashboard():
    if not available_port(PORTS.start):
        systemctl = shutil.which("systemctl")
        if not systemctl or subprocess.run([systemctl, "--user", "is-active", "--quiet", DASHBOARD_UNIT],
                                           timeout=5).returncode or not is_dashboard(PORTS.start):
            raise RuntimeError(f"Port {PORTS.start} is in use. Stop the existing dashboard before enabling the service.")
    python = prepare_project_python()
    enable_systemd_units({DASHBOARD_UNIT: dashboard_service(python)}, DASHBOARD_UNIT, "dashboard")
    return wait_for_background_dashboard()


def arm_paper_scheduler(enable_linux=False):
    if not enable_linux or not sys.platform.startswith("linux"):
        return None
    account = PROJECT / "private" / "competition"
    has_state = (account / "account.json").is_file() and (account / "live" / "state.json").is_file()
    if not has_state:
        raise RuntimeError("Migrate the confirmed competition account and its saved state before enabling paper startup.")
    return arm_linux_scheduler()


def report_message(message, *, error=False):
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        print(message, file=stream, flush=True)
    elif error:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with (RUNTIME / "launcher.log").open("a", encoding="utf-8") as log:
            print(message, file=log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dashboard-only", action="store_true", help="Open the dashboard without arming the scheduler")
    modes.add_argument("--enable-paper", action="store_true", help="Enable the paper scheduler after migration to Linux")
    modes.add_argument("--enable-background", action="store_true", help="Enable the always-on dashboard service on Linux")
    modes.add_argument("--install-shortcut", action="store_true", help="Install the console-free Windows desktop shortcut")
    parser.add_argument("--no-browser", action="store_true", help="Print the dashboard URL without opening a tab")
    args = parser.parse_args(argv)
    if args.install_shortcut:
        try:
            with startup_lock():
                report_message(install_windows_shortcut())
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            report_message(f"Shortcut installation failed: {exc}", error=True)
            return 1
        return 0
    if args.enable_paper and not sys.platform.startswith("linux"):
        parser.error("--enable-paper is for the Linux scheduler")
    if args.enable_background and not sys.platform.startswith("linux"):
        parser.error("--enable-background requires Linux with systemd")
    try:
        with startup_lock():
            port = enable_linux_dashboard() if args.enable_background else dashboard()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        report_message(f"Dashboard startup failed: {exc}", error=True)
        return 1

    url = f"http://127.0.0.1:{port}/"
    report_message(url)
    if not args.no_browser:
        webbrowser.open(url, new=2)
    if args.enable_paper:
        try:
            with startup_lock():
                note = arm_paper_scheduler(enable_linux=args.enable_paper)
            if note:
                report_message(note)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            report_message(f"Paper scheduler startup failed: {exc}", error=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
