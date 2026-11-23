import importlib.util
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "start.py"
    spec = importlib.util.spec_from_file_location("competition_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PROJECT", tmp_path / "project with spaces")
    monkeypatch.setattr(module, "RUNTIME", module.PROJECT / "private/launcher")
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    return module


@pytest.mark.parametrize("platform,relative", [
    ("linux", ".venv/bin/python"), ("darwin", ".venv/bin/python"), ("win32", ".venv/Scripts/python.exe"),
])
def test_first_launch_installs_local_environment(launcher, monkeypatch, platform, relative):
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform=platform, version_info=(3, 12), executable="python"))
    monkeypatch.setattr(launcher, "report_message", lambda *args, **kwargs: None)
    launcher.RUNTIME.mkdir(parents=True)
    calls = []

    def run(args, **kwargs):
        assert kwargs["cwd"] == launcher.PROJECT
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.prepare_project_python() == launcher.PROJECT / relative
    python = str(launcher.PROJECT / relative)
    assert calls[0] == ["python", "-m", "venv", str(launcher.PROJECT / ".venv")]
    assert calls[1:3] == [[python, "-m", "pip", "install", "-r", "requirements.lock"],
                          [python, "-m", "pip", "install", "--no-deps", "-e", "."]]
    assert calls[3][:2] == [python, "-c"]


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_default_launch_only_opens_dashboard(launcher, monkeypatch, platform):
    monkeypatch.setattr(launcher.sys, "platform", platform)
    monkeypatch.setattr(launcher, "startup_lock", nullcontext)
    monkeypatch.setattr(launcher, "dashboard", lambda: 8876)
    monkeypatch.setattr(launcher, "report_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "arm_paper_scheduler", lambda **kwargs: pytest.fail("Dashboard armed trading"))
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url, **kwargs: opened.append(url))

    assert launcher.main([]) == 0
    assert opened == ["http://127.0.0.1:8876/"]


def test_running_dashboard_reuses_port_before_environment_setup(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "is_dashboard", lambda port: port == 8877)
    monkeypatch.setattr(launcher, "prepare_project_python", lambda: pytest.fail("Running dashboard was not reused"))
    assert launcher.dashboard() == 8877


def test_linux_background_installs_only_dashboard_service(launcher, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(launcher, "available_port", lambda port: port == 8876)
    monkeypatch.setattr(launcher, "prepare_project_python", launcher.project_python)
    monkeypatch.setattr(launcher, "wait_for_background_dashboard", lambda: 8876)
    monkeypatch.setattr(launcher.shutil, "which", lambda command: "/usr/bin/systemctl")
    calls = []

    def run(args, **kwargs):
        calls.append(args[2:])
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(launcher.subprocess, "run", run)

    assert launcher.enable_linux_dashboard() == 8876
    assert calls == [["show-environment"], ["daemon-reload"], ["enable", "--now", "moomoo-dashboard.service"]]
    root = tmp_path / "config/systemd/user"
    assert [path.name for path in root.iterdir()] == ["moomoo-dashboard.service"]
    content = (root / "moomoo-dashboard.service").read_text()
    assert "Restart=always" in content
    assert "-m dual_strategy.cli --project " in content
    assert "serve --live-votes --port 8876" in content
    assert "--execute" not in content


def test_launcher_waits_for_enabled_background_service(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "is_dashboard", lambda port: False)
    monkeypatch.setattr(launcher, "linux_dashboard_enabled", lambda: True)
    monkeypatch.setattr(launcher, "wait_for_background_dashboard", lambda: 8876)
    monkeypatch.setattr(launcher, "prepare_project_python", lambda: pytest.fail("Started a second dashboard"))
    assert launcher.dashboard() == 8876


def test_fresh_linux_launcher_requires_explicit_scheduler_enable(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "arm_linux_scheduler", lambda: pytest.fail("Fresh launch armed trading"))
    assert launcher.arm_paper_scheduler() is None
    with pytest.raises(RuntimeError, match="Migrate"):
        launcher.arm_paper_scheduler(enable_linux=True)


def test_explicit_linux_enable_installs_a_user_timer(launcher, monkeypatch, tmp_path):
    account = launcher.PROJECT / "private/competition"
    (account / "live").mkdir(parents=True)
    (account / "account.json").write_text("{}", encoding="utf-8")
    (account / "live/state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(launcher, "project_python", lambda: launcher.PROJECT / ".venv/bin/python")
    monkeypatch.setattr(launcher.shutil, "which", lambda command: "/usr/bin/systemctl")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.arm_paper_scheduler(enable_linux=True)
    assert [command[2:] for command in commands] == [
        ["show-environment"], ["daemon-reload"], ["enable", "--now", "moomoo-competition-start.timer"],
    ]
    config = tmp_path / "config/systemd/user"
    service = config / "moomoo-competition-start.service"
    content = service.read_text(encoding="utf-8")
    executable = str(launcher.PROJECT / ".venv/bin/python").replace("\\", "\\\\")
    assert f'ExecStart=:"{executable}"' in content
    assert " --run" in content
    assert "UMask=0077" in content
    assert "OnUnitInactiveSec=60s" in (config / "moomoo-competition-start.timer").read_text(encoding="utf-8")

    service.write_text("owned by another checkout", encoding="utf-8")
    commands.clear()
    with pytest.raises(RuntimeError, match="different scheduler"):
        launcher.arm_paper_scheduler(enable_linux=True)
    assert [command[2:] for command in commands] == [["show-environment"]]
    assert service.read_text(encoding="utf-8") == "owned by another checkout"
