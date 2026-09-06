from pathlib import Path

import pytest

from rpi_mcp.config import ConfigurationError, config_file_path, load_settings


def test_load_settings_uses_configured_hosts_and_flags() -> None:
    settings = load_settings(
        {
            "RPI_MCP_RPI5_HOST": "rpi5.lan",
            "RPI_MCP_RPI3_HOST": "192.168.0.20",
            "RPI_MCP_RPI5_SSH_USER": "operator",
            "RPI_MCP_RPI5_SSH_PORT": "2222",
            "RPI_MCP_RPI3_SSH_USER": "pi",
            "RPI_MCP_RPI3_SSH_PORT": "2200",
            "RPI_MCP_ENABLE_COMMANDS": "yes",
            "RPI_MCP_ENABLE_POWER_ACTIONS": "true",
        }
    )

    rpi5 = settings.device("rpi5")
    rpi3 = settings.device("RPI3BPLUS")
    assert rpi5.host == "rpi5.lan"
    assert rpi5.username == "operator"
    assert rpi5.port == 2222
    assert rpi3.architecture == "arm32"
    assert rpi3.username == "pi"
    assert rpi3.port == 2200
    assert settings.commands_enabled is True
    assert settings.power_actions_enabled is True


def test_config_file_path_honors_an_explicit_environment_file(tmp_path: Path) -> None:
    expected_path = tmp_path / "rpi-mcp.env"

    assert config_file_path({"RPI_MCP_ENV_FILE": str(expected_path)}) == expected_path


def test_load_settings_requires_hosts() -> None:
    with pytest.raises(ConfigurationError, match="RPI_MCP_RPI5_HOST is required"):
        load_settings({})


def test_load_settings_rejects_an_unsafe_hostname() -> None:
    with pytest.raises(ConfigurationError, match="IP address or DNS hostname"):
        load_settings({"RPI_MCP_RPI5_HOST": "rpi5; reboot"})


def test_load_settings_rejects_an_invalid_boolean() -> None:
    with pytest.raises(ConfigurationError, match="must be one of"):
        load_settings(
            {
                "RPI_MCP_RPI5_HOST": "192.168.0.146",
                "RPI_MCP_RPI3_HOST": "192.168.0.136",
                "RPI_MCP_ENABLE_COMMANDS": "sometimes",
            }
        )