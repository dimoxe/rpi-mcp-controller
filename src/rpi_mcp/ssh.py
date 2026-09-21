"""A small, shell-free adapter around the local OpenSSH client."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from .config import Device, Settings


class SSHUnavailableError(RuntimeError):
    """Raised when the local OpenSSH client cannot be found."""


@dataclass(frozen=True, slots=True)
class SSHResult:
    """The bounded output returned by one SSH invocation."""

    device: Device
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "device": self.device.as_dict(),
            "ok": self.exit_code == 0 and not self.timed_out,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def build_ssh_command(
    ssh_executable: str, settings: Settings, device: Device, remote_command: str
) -> list[str]:
    """Build an SSH argv list without passing any values through a local shell."""

    command = [
        ssh_executable,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"ConnectTimeout={settings.connect_timeout_seconds}",
        "-p",
        str(device.port),
    ]
    if settings.identity_file is not None:
        command.extend(["-i", str(settings.identity_file)])
    if settings.known_hosts_file is not None:
        command.extend(["-o", f"UserKnownHostsFile={settings.known_hosts_file}"])
    command.extend([f"{device.username}@{device.host}", remote_command])
    return command


class SSHExecutor:
    """Execute one remote command with predictable timeouts and captured output."""

    def __init__(
        self,
        settings: Settings,
        *,
        ssh_executable: str | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._settings = settings
        self._ssh_executable = ssh_executable
        self._run = run

    def execute(
        self, device: Device, remote_command: str, *, timeout_seconds: int | None = None
    ) -> SSHResult:
        if "\x00" in remote_command:
            raise ValueError("Remote commands cannot contain null bytes.")

        executable = self._ssh_executable or shutil.which("ssh")
        if executable is None:
            raise SSHUnavailableError(
                "OpenSSH was not found. Install the Windows OpenSSH Client feature first."
            )

        timeout = timeout_seconds or self._settings.command_timeout_seconds
        command = build_ssh_command(executable, self._settings, device, remote_command)
        try:
            completed = self._run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return SSHResult(
                device=device,
                exit_code=None,
                stdout=_as_text(error.stdout),
                stderr=_as_text(error.stderr),
                timed_out=True,
            )

        return SSHResult(
            device=device,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""