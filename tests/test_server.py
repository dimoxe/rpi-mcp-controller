import os
import shutil
import subprocess
from pathlib import Path

import pytest
from mcp import Client

from rpi_mcp import server


@pytest.fixture(autouse=True)
def configured_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPI_MCP_RPI5_HOST", "192.0.2.5")
    monkeypatch.setenv("RPI_MCP_RPI3_HOST", "192.0.2.3")


def test_server_object_imports_without_starting_stdio() -> None:
    assert server.mcp is not None


def test_service_control_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RPI_MCP_ENABLE_COMMANDS", raising=False)

    with pytest.raises(PermissionError, match="Command control is disabled"):
        server.control_service("rpi5", "ssh", "restart")


def test_service_control_rejects_an_invalid_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPI_MCP_ENABLE_COMMANDS", "1")

    with pytest.raises(ValueError, match="action must be one of"):
        server.control_service("rpi5", "ssh", "restart; reboot")


def test_power_action_rejects_an_invalid_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPI_MCP_ENABLE_POWER_ACTIONS", "1")

    with pytest.raises(ValueError, match="action must be one of"):
        server.power_action("rpi5", "reboot; shutdown", "CONFIRM")


@pytest.mark.anyio
async def test_discovery_tool_runs_through_the_mcp_protocol() -> None:
    async with Client(server.mcp) as client:
        result = await client.call_tool("list_raspberry_pis", {})

    devices = result.structured_content["devices"]
    assert [device["name"] for device in devices] == ["rpi5", "rpi3bplus"]

def _capture_remote(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_execute_remote(settings, device, command, *, timeout_seconds=None, stdin=None):
        captured.update(device=device.name, command=command, stdin=stdin)
        return {"ok": True}

    monkeypatch.setattr(server, "execute_remote", fake_execute_remote)
    return captured


def test_service_control_uses_non_interactive_sudo_without_a_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPI_MCP_ENABLE_COMMANDS", "1")
    monkeypatch.delenv("RPI_MCP_RPI5_SUDO_PASSWORD", raising=False)
    captured = _capture_remote(monkeypatch)

    server.control_service("rpi5", "ssh", "restart")

    assert captured == {"device": "rpi5", "command": "sudo -n systemctl restart ssh", "stdin": None}


def test_service_control_sends_the_sudo_password_over_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPI_MCP_ENABLE_COMMANDS", "1")
    monkeypatch.setenv("RPI_MCP_RPI5_SUDO_PASSWORD", "s3cret")
    captured = _capture_remote(monkeypatch)

    server.control_service("rpi5", "ssh", "restart")

    assert captured == {
        "device": "rpi5",
        "command": "sudo -S -p '' systemctl restart ssh",
        "stdin": "s3cret\n",
    }


def test_power_action_sends_the_sudo_password_over_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPI_MCP_ENABLE_POWER_ACTIONS", "1")
    monkeypatch.setenv("RPI_MCP_RPI5_SUDO_PASSWORD", "s3cret")
    captured = _capture_remote(monkeypatch)

    server.power_action("rpi5", "reboot", "CONFIRM")

    assert captured["command"] == "sudo -S -p '' systemctl reboot"
    assert captured["stdin"] == "s3cret\n"


def _enable_run_command_sudo(monkeypatch: pytest.MonkeyPatch, password: str | None) -> None:
    monkeypatch.setenv("RPI_MCP_ENABLE_COMMANDS", "1")
    monkeypatch.setenv("RPI_MCP_ENABLE_RUN_COMMAND_SUDO", "1")
    if password is None:
        monkeypatch.delenv("RPI_MCP_RPI5_SUDO_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("RPI_MCP_RPI5_SUDO_PASSWORD", password)


def test_run_command_passes_sudo_through_unchanged_without_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RPI_MCP_ENABLE_COMMANDS", "1")
    monkeypatch.delenv("RPI_MCP_ENABLE_RUN_COMMAND_SUDO", raising=False)
    monkeypatch.setenv("RPI_MCP_RPI5_SUDO_PASSWORD", "s3cret")
    captured = _capture_remote(monkeypatch)

    server.run_command("rpi5", "sudo docker ps")

    assert captured == {"device": "rpi5", "command": "sudo docker ps", "stdin": None}


def test_run_command_without_sudo_never_receives_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_run_command_sudo(monkeypatch, "s3cret")
    captured = _capture_remote(monkeypatch)

    server.run_command("rpi5", "pseudocode --version")

    assert captured == {"device": "rpi5", "command": "pseudocode --version", "stdin": None}


def test_run_command_without_a_configured_password_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_run_command_sudo(monkeypatch, None)
    captured = _capture_remote(monkeypatch)

    server.run_command("rpi5", "sudo docker ps")

    assert captured == {"device": "rpi5", "command": "sudo docker ps", "stdin": None}


def test_run_command_supplies_the_sudo_password_through_askpass_and_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_run_command_sudo(monkeypatch, "s3cret")
    captured = _capture_remote(monkeypatch)

    server.run_command("rpi5", "cd /srv/flights && sudo docker compose up -d")

    command = str(captured["command"])
    assert captured["stdin"] == "s3cret\n"
    assert "s3cret" not in command
    assert "SUDO_ASKPASS" in command
    assert command.endswith("\ncd /srv/flights && sudo docker compose up -d")


_FAKE_SUDO = """#!/bin/sh
# Stands in for sudo: demands -A, checks the askpass answer, then runs the command.
[ "$1" = "-A" ] || { echo "fake sudo: expected -A" >&2; exit 90; }
shift
answer=$("$SUDO_ASKPASS")
[ "$answer" = "$EXPECTED_SUDO_PASSWORD" ] || { echo "fake sudo: bad password" >&2; exit 91; }
"$@"
"""


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize(
    ("command", "expected_stdout"),
    [
        ("sudo echo root-ok", "root-ok\n"),
        ("echo piped | sudo cat", "piped\n"),
        ("sudo echo one && sudo echo two", "one\ntwo\n"),
        ("sudo cat", ""),
    ],
)
def test_askpass_prelude_runs_sudo_without_exposing_the_password(
    tmp_path: Path, command: str, expected_stdout: str
) -> None:
    password = r" it's $ecret \ "
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sudo = bin_dir / "sudo"
    fake_sudo.write_text(_FAKE_SUDO, newline="\n")
    fake_sudo.chmod(0o755)
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "EXPECTED_SUDO_PASSWORD": password,
    }

    completed = subprocess.run(
        ["sh", "-c", server.with_sudo_askpass(command)],
        input=f"{password}\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert completed.stderr == ""
    assert completed.returncode == 0
    assert completed.stdout == expected_stdout
    assert password.strip() not in completed.stdout
    assert list(runtime_dir.iterdir()) == []


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_askpass_prelude_keeps_the_command_exit_status(tmp_path: Path) -> None:
    env = {**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)}

    completed = subprocess.run(
        ["sh", "-c", server.with_sudo_askpass("exit 7")],
        input="pw\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 7
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_agents_receive_the_command_notes_through_the_mcp_protocol() -> None:
    async with Client(server.mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    description = tools["run_command"].description or ""
    assert "write plain `sudo <command>`" in description
    assert "never use `sudo -S`" in description
