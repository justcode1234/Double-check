import importlib.util
import subprocess
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
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    return module


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
