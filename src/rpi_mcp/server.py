"""MCP tools for safely accessing and controlling two Raspberry Pis over SSH."""

from __future__ import annotations

import re
import shlex
from typing import Literal

from mcp.server import MCPServer

from .config import Device, Settings, load_settings
from .ssh import SSHExecutor


_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9@_.-]{1,128}$")
_MAX_FILE_BYTES = 1_000_000
_MAX_LOG_LINES = 500
_MAX_COMMAND_LENGTH = 4_096
_SERVICE_ACTIONS = frozenset({"start", "stop", "restart"})
_POWER_ACTIONS = frozenset({"reboot", "shutdown"})
_SUDO_WORD_RE = re.compile(r"(?<![\w.-])sudo(?![\w.-])")

# Prepended to a run_command command that uses sudo when a password is configured.
# The password arrives as the first line of SSH stdin, so it never appears in argv,
# a process listing, or the environment of the command. It is written to an
# owner-only directory (tmpfs under XDG_RUNTIME_DIR when available) and handed to
# sudo through SUDO_ASKPASS, which leaves stdin free: pipelines such as
# `echo x | sudo tee f` work, and a sudo that needs no password cannot leak it
# into the command's stdin. The directory is removed when the shell exits.
_SUDO_ASKPASS_PRELUDE = r"""IFS= read -r __rpi_mcp_pw || exit 125
__rpi_mcp_dir=$(umask 077 && mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/rpi-mcp.XXXXXX") || exit 125
trap 'rm -rf "$__rpi_mcp_dir"' EXIT
trap 'rm -rf "$__rpi_mcp_dir"; exit 129' HUP
trap 'rm -rf "$__rpi_mcp_dir"; exit 130' INT
trap 'rm -rf "$__rpi_mcp_dir"; exit 143' TERM
(umask 077 && printf '%s\n' "$__rpi_mcp_pw" > "$__rpi_mcp_dir/pw" && printf '#!/bin/sh\nexec cat "$(dirname -- "$0")/pw"\n' > "$__rpi_mcp_dir/askpass" && chmod 700 "$__rpi_mcp_dir/askpass") || exit 125
unset __rpi_mcp_pw
SUDO_ASKPASS="$__rpi_mcp_dir/askpass"
export SUDO_ASKPASS
sudo() { command sudo -A "$@"; }"""

_AGENT_COMMAND_NOTES = """\
How to run commands with run_command:
- Commands run non-interactively over SSH in the SSH user's login shell. Stdin is
  empty and there is no terminal: never run anything that prompts. Use flags such as
  -y, --no-pager, or `sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y ...`.
- For root, write plain `sudo <command>`. When the operator has enabled sudo for
  run_command, the server supplies the password itself. Never put a password in a
  command, and never use `sudo -S`, `sudo -A`, `sudo -n`, or `echo ... | sudo -S`.
- Every `sudo` the shell invokes directly works, including several per command and
  pipelines like `echo text | sudo tee /etc/file`. A sudo started by another program
  (`xargs sudo`, `find -exec sudo`, a script) does not; wrap the whole step instead:
  `sudo sh -c 'cd /srv/app && ./deploy.sh'`. Do not start the command with `exec`.
- If stderr says "a terminal is required" or "a password is required", sudo is not
  enabled for run_command on that Pi. Tell the operator; do not try workarounds.
- `ok` and `exit_code` reflect only the last command of the shell line. Chain steps
  with `&&` (or start with `set -e`) so a failed step is reported, and read stderr.
- The timeout is at most 120 seconds. Start long jobs detached (for example
  `docker compose up -d`, or `nohup cmd > /tmp/job.log 2>&1 &`) and poll their logs.
"""

_STATUS_COMMAND = """
printf 'hostname: '; hostname
printf 'kernel: '; uname -srmo
printf 'uptime: '; uptime -p
printf '\\nmemory:\\n'; free -h
printf '\\ndisk:\\n'; df -h /
printf '\\ntemperature: '
if command -v vcgencmd >/dev/null 2>&1; then
    vcgencmd measure_temp
elif test -r /sys/class/thermal/thermal_zone0/temp; then
    awk '{printf "temp=%.1f\\x27C\\n", $1 / 1000}' /sys/class/thermal/thermal_zone0/temp
else
    printf 'unavailable\\n'
fi
""".strip()

mcp = MCPServer(
    "Raspberry Pi SSH Controller",
    instructions=(
        "Use the named Raspberry Pi tools for status, logs, and services. "
        "State-changing commands are disabled unless the operator explicitly enables them.\n\n"
        + _AGENT_COMMAND_NOTES
    ),
)


def execute_remote(
    settings: Settings,
    device: Device,
    command: str,
    *,
    timeout_seconds: int | None = None,
    stdin: str | None = None,
) -> dict[str, object]:
    """Run a remote command through the common SSH transport."""

    return SSHExecutor(settings).execute(
        device, command, timeout_seconds=timeout_seconds, stdin=stdin
    ).as_dict()


def execute_sudo(settings: Settings, device: Device, command: str) -> dict[str, object]:
    """Run a command as root, answering sudo's password prompt only when one is configured."""

    if device.sudo_password is None:
        return execute_remote(settings, device, f"sudo -n {command}")
    return execute_remote(
        settings, device, f"sudo -S -p '' {command}", stdin=f"{device.sudo_password}\n"
    )


def with_sudo_askpass(command: str) -> str:
    """Prefix a shell command so its sudo calls read the password from SSH stdin."""

    return f"{_SUDO_ASKPASS_PRELUDE}\n{command}"


@mcp.tool()
def list_raspberry_pis() -> dict[str, object]:
    """List the named Raspberry Pis available to this MCP server."""

    settings = load_settings()
    return {"devices": [device.as_dict() for device in settings.devices]}


@mcp.tool()
def get_raspberry_pi_status(device: Literal["rpi5", "rpi3bplus"]) -> dict[str, object]:
    """Read hostname, kernel, uptime, memory, disk, and CPU temperature from a Pi."""

    settings = load_settings()
    return execute_remote(settings, settings.device(device), _STATUS_COMMAND)


@mcp.tool()
def read_text_file(
    device: Literal["rpi5", "rpi3bplus"], path: str, max_bytes: int = 65_536
) -> dict[str, object]:
    """Read up to one MiB of a text file from a named Pi without modifying it."""

    _validate_path(path)
    byte_limit = _bounded_integer("max_bytes", max_bytes, minimum=1, maximum=_MAX_FILE_BYTES)
    settings = load_settings()
    command = f"head -c {byte_limit} -- {shlex.quote(path)}"
    return execute_remote(settings, settings.device(device), command)


@mcp.tool()
def get_service_status(
    device: Literal["rpi5", "rpi3bplus"], service: str
) -> dict[str, object]:
    """Get detailed systemd status for one service on a Pi."""

    service_name = _validate_service_name(service)
    settings = load_settings()
    command = f"systemctl status --no-pager --full {shlex.quote(service_name)}"
    return execute_remote(settings, settings.device(device), command)


@mcp.tool()
def get_service_logs(
    device: Literal["rpi5", "rpi3bplus"], service: str, lines: int = 100
) -> dict[str, object]:
    """Read the newest journal entries for one systemd service on a Pi."""

    service_name = _validate_service_name(service)
    line_limit = _bounded_integer("lines", lines, minimum=1, maximum=_MAX_LOG_LINES)
    settings = load_settings()
    command = f"journalctl --no-pager -n {line_limit} -u {shlex.quote(service_name)}"
    return execute_remote(settings, settings.device(device), command)


@mcp.tool()
def control_service(
    device: Literal["rpi5", "rpi3bplus"],
    service: str,
    action: Literal["start", "stop", "restart"],
) -> dict[str, object]:
    """Start, stop, or restart a systemd service after command control is explicitly enabled."""

    settings = load_settings()
    _require_commands_enabled(settings)
    service_name = _validate_service_name(service)
    service_action = _validate_action("action", action, _SERVICE_ACTIONS)
    command = f"systemctl {service_action} {shlex.quote(service_name)}"
    return execute_sudo(settings, settings.device(device), command)


@mcp.tool(
    description=(
        "Run an arbitrary shell command on a Pi after the operator enables command "
        "control.\n\n" + _AGENT_COMMAND_NOTES
    )
)
def run_command(
    device: Literal["rpi5", "rpi3bplus"], command: str, timeout_seconds: int = 30
) -> dict[str, object]:
    """Run an arbitrary remote command only after the operator enables command control."""

    settings = load_settings()
    _require_commands_enabled(settings)
    if not command.strip() or "\x00" in command or len(command) > _MAX_COMMAND_LENGTH:
        raise ValueError(f"command must contain 1 to {_MAX_COMMAND_LENGTH} non-null characters.")
    timeout = _bounded_integer("timeout_seconds", timeout_seconds, minimum=1, maximum=120)
    target = settings.device(device)
    if (
        settings.run_command_sudo_enabled
        and target.sudo_password is not None
        and _SUDO_WORD_RE.search(command)
    ):
        return execute_remote(
            settings,
            target,
            with_sudo_askpass(command),
            timeout_seconds=timeout,
            stdin=f"{target.sudo_password}\n",
        )
    return execute_remote(settings, target, command, timeout_seconds=timeout)


@mcp.tool()
def power_action(
    device: Literal["rpi5", "rpi3bplus"],
    action: Literal["reboot", "shutdown"],
    confirm: Literal["CONFIRM"],
) -> dict[str, object]:
    """Reboot or shut down a Pi only after a separate power-control opt-in and confirmation."""

    settings = load_settings()
    if not settings.power_actions_enabled:
        raise PermissionError(
            "Power actions are disabled. Set RPI_MCP_ENABLE_POWER_ACTIONS=1 to enable them."
        )
    if confirm != "CONFIRM":
        raise ValueError("Set confirm to CONFIRM to run a power action.")
    power_command = _validate_action("action", action, _POWER_ACTIONS)
    return execute_sudo(settings, settings.device(device), f"systemctl {power_command}")


def _validate_service_name(service: str) -> str:
    service_name = service.strip()
    if not _SERVICE_NAME_RE.fullmatch(service_name):
        raise ValueError("service must be a systemd unit name without spaces or shell syntax.")
    return service_name


def _validate_path(path: str) -> None:
    if not path.strip() or "\x00" in path or path.startswith("-"):
        raise ValueError("path must be a non-empty file path and cannot start with '-'.")


def _validate_action(name: str, value: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}.")
    return value


def _bounded_integer(name: str, value: int, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    return value


def _require_commands_enabled(settings: Settings) -> None:
    if not settings.commands_enabled:
        raise PermissionError(
            "Command control is disabled. Set RPI_MCP_ENABLE_COMMANDS=1 to enable it."
        )


if __name__ == "__main__":
    mcp.run()