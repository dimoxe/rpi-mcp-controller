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
