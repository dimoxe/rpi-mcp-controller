"""Configuration for the Raspberry Pi SSH MCP server."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from platformdirs import user_config_dir


_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,251}$")
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})
_ENV_FILE_VARIABLE = "RPI_MCP_ENV_FILE"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ENV_FILE = _SOURCE_ROOT / ".env"


class ConfigurationError(ValueError):
    """Raised when an environment setting cannot safely form an SSH argument."""


@dataclass(frozen=True, slots=True)
class Device:
    """A named Raspberry Pi that can be addressed by the server."""

    name: str
    label: str
    host: str
    architecture: str
    username: str
    port: int

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "label": self.label,
            "host": self.host,
            "architecture": self.architecture,
            "username": self.username,
            "port": str(self.port),
        }


@dataclass(frozen=True, slots=True)
class Settings:
    """SSH settings and the fixed device inventory."""

    devices: tuple[Device, ...]
    identity_file: Path | None
    known_hosts_file: Path | None
    connect_timeout_seconds: int
    command_timeout_seconds: int
    commands_enabled: bool
    power_actions_enabled: bool

    def device(self, name: str) -> Device:
        normalized_name = name.lower()
        for device in self.devices:
            if device.name == normalized_name:
                return device
        names = ", ".join(device.name for device in self.devices)
        raise ValueError(f"Unknown device {name!r}. Choose one of: {names}.")


def config_file_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the operator-owned environment file used by this installation."""

    values = os.environ if env is None else env
    explicit_path = _value(values, _ENV_FILE_VARIABLE, "")
    if explicit_path:
        return Path(explicit_path).expanduser()
    if (_SOURCE_ROOT / "pyproject.toml").is_file() and _SOURCE_ENV_FILE.is_file():
        return _SOURCE_ENV_FILE
    return Path(user_config_dir("rpi-mcp", appauthor=False)) / ".env"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from the environment without persisting any credentials."""

    if env is None:
        load_dotenv(config_file_path(), override=False)
        values = os.environ
    else:
        values = env
    rpi5_host = _host(values, "RPI_MCP_RPI5_HOST")
    rpi3_host = _host(values, "RPI_MCP_RPI3_HOST")

    return Settings(
        devices=(
            Device(
                "rpi5",
                "Raspberry Pi 5",
                rpi5_host,
                "arm64",
                _username(_value(values, "RPI_MCP_RPI5_SSH_USER", "pi"), "RPI_MCP_RPI5_SSH_USER"),
                _integer(values, "RPI_MCP_RPI5_SSH_PORT", 22, minimum=1, maximum=65535),
            ),
            Device(
                "rpi3bplus",
                "Raspberry Pi 3B+",
                rpi3_host,
                "arm32",
                _username(_value(values, "RPI_MCP_RPI3_SSH_USER", "pi"), "RPI_MCP_RPI3_SSH_USER"),
                _integer(values, "RPI_MCP_RPI3_SSH_PORT", 22, minimum=1, maximum=65535),
            ),
        ),
        identity_file=_optional_path(values, "RPI_MCP_SSH_IDENTITY_FILE"),
        known_hosts_file=_optional_path(values, "RPI_MCP_KNOWN_HOSTS_FILE"),
        connect_timeout_seconds=_integer(
            values, "RPI_MCP_CONNECT_TIMEOUT_SECONDS", 10, minimum=1, maximum=120
        ),
        command_timeout_seconds=_integer(
            values, "RPI_MCP_COMMAND_TIMEOUT_SECONDS", 30, minimum=1, maximum=120
        ),
        commands_enabled=_boolean(values, "RPI_MCP_ENABLE_COMMANDS", False),
        power_actions_enabled=_boolean(values, "RPI_MCP_ENABLE_POWER_ACTIONS", False),
    )


def _value(values: Mapping[str, str], name: str, default: str) -> str:
    return values.get(name, default).strip()


def _host(values: Mapping[str, str], name: str) -> str:
    value = _value(values, name, "")
    if not value:
        raise ConfigurationError(f"{name} is required.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(value):
            raise ConfigurationError(f"{name} must be an IP address or DNS hostname.")
    return value


def _username(value: str, name: str) -> str:
    if not _USERNAME_RE.fullmatch(value):
        raise ConfigurationError(f"{name} must be a valid Linux username.")
    return value


def _integer(
    values: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = _value(values, name, str(default))
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _optional_path(values: Mapping[str, str], name: str) -> Path | None:
    value = _value(values, name, "")
    return Path(value).expanduser() if value else None


def _boolean(values: Mapping[str, str], name: str, default: bool) -> bool:
    value = _value(values, name, "true" if default else "false").lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be one of: 1, 0, true, false, yes, no, on, off.")