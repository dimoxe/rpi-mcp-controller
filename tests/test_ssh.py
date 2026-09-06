from types import SimpleNamespace
from pathlib import Path

from rpi_mcp.config import load_settings
from rpi_mcp.ssh import SSHExecutor, build_ssh_command


def test_build_ssh_command_uses_strict_host_verification() -> None:
    settings = load_settings(
        {
            "RPI_MCP_RPI5_HOST": "192.168.0.146",
            "RPI_MCP_RPI3_HOST": "192.168.0.136",
            "RPI_MCP_RPI5_SSH_USER": "operator",
            "RPI_MCP_RPI5_SSH_PORT": "2222",
            "RPI_MCP_RPI3_SSH_USER": "operator3",
            "RPI_MCP_RPI3_SSH_PORT": "2200",
            "RPI_MCP_SSH_IDENTITY_FILE": "C:/keys/rpi",
            "RPI_MCP_KNOWN_HOSTS_FILE": "C:/keys/known_hosts",
        }
    )

    command = build_ssh_command("ssh", settings, settings.device("rpi5"), "uname -a")

    assert command == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-p",
        "2222",
        "-i",
        str(Path("C:/keys/rpi")),
        "-o",
        f"UserKnownHostsFile={Path('C:/keys/known_hosts')}",
        "operator@192.168.0.146",
        "uname -a",
    ]

    rpi3_command = build_ssh_command("ssh", settings, settings.device("rpi3bplus"), "uname -m")

    assert rpi3_command[7:9] == ["-p", "2200"]
    assert rpi3_command[-2:] == ["operator3@192.168.0.136", "uname -m"]


def test_executor_returns_command_output_without_a_local_shell() -> None:
    settings = load_settings(
        {
            "RPI_MCP_RPI5_HOST": "192.168.0.146",
            "RPI_MCP_RPI3_HOST": "192.168.0.136",
        }
    )
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="Linux\n", stderr="")

    result = SSHExecutor(settings, ssh_executable="ssh", run=fake_run).execute(
        settings.device("rpi3bplus"), "uname -s"
    )

    assert result.as_dict()["ok"] is True
    assert result.stdout == "Linux\n"
    assert captured["kwargs"] == {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 30,
        "check": False,
    }