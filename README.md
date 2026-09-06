# Raspberry Pi SSH MCP Controller

This is a local Model Context Protocol (MCP) server that accesses Raspberry
Pis over SSH from VS Code. It follows Arm's `uv`-managed Python MCP workflow,
updated to the current MCP Python SDK and its recommended `stdio` transport.
It deliberately does not expose an Internet-facing SSE/ngrok endpoint and can
be installed as an independent `uv` tool.

| Name | Device | Configuration | Architecture |
| --- | --- | --- | --- |
| `rpi5` | Raspberry Pi 5 | `RPI_MCP_RPI5_*` | `arm64` |
| `rpi3bplus` | Raspberry Pi 3B+ | `RPI_MCP_RPI3_*` | `arm32` |

## Package layout

Keep the protocol boundary thin and place lower-level concerns behind it:

- `server.py`: MCP tool schemas, validation, and tool registration only.
- `ssh.py`: shell-free OpenSSH command construction and process execution.
- `config.py`: configuration discovery, parsing, validation, and device inventory.
- `cli.py`: the installable `rpi-mcp` entry point and config-template bootstrap.
- `templates/default.env`: non-secret configuration template packaged with the wheel.

This keeps the SSH adapter testable without an MCP host and prevents LAN
addresses, usernames, private-key paths, and opt-in controls from being
embedded in the distributable artifact.

## Install as a uv tool

Build a wheel and source distribution from the source checkout:

```powershell
uv build
```

Install it locally from that source project:

```powershell
uv tool install --from . rpi-mcp-controller
```

For a released or copied artifact, install the generated wheel instead:

```powershell
uv tool install path/to/rpi_mcp_controller-<version>-py3-none-any.whl
```

Create the operator-owned configuration file and print its location:

```powershell
rpi-mcp init-config
rpi-mcp config-path
```

The installed tool uses a platform-specific per-user configuration directory.
Set `RPI_MCP_ENV_FILE` to use a different file, such as a managed secrets
location. Source-checkout development continues to load `.env` when it exists.

## What it provides

- `list_raspberry_pis`: show the configured device inventory.
- `get_raspberry_pi_status`: hostname, kernel, uptime, memory, disk, and CPU temperature.
- `read_text_file`: bounded, read-only remote file access.
- `get_service_status` and `get_service_logs`: inspect a systemd service.
- `control_service`: start, stop, or restart a service after an explicit opt-in.
- `run_command`: arbitrary remote command execution after an explicit opt-in.
- `power_action`: reboot or shut down a device after a separate opt-in and a `CONFIRM` argument.

## Security model

The server runs as a child process of VS Code over stdio: it listens on no TCP
port. Each tool invocation calls the local OpenSSH client with `BatchMode=yes`
and `StrictHostKeyChecking=yes`; password prompts are disabled and an unknown
host key is rejected. Use an SSH key or agent, not a password.

Read-only tools are available by default. `control_service` and `run_command`
remain disabled until `RPI_MCP_ENABLE_COMMANDS=1` is set. `power_action` needs
its own `RPI_MCP_ENABLE_POWER_ACTIONS=1` setting. Enabling `run_command` gives
the connected MCP client arbitrary access available to the SSH user, so only
enable it for a trusted local VS Code session.

The service and power tools use `sudo -n`, which refuses a password prompt.
Grant only the exact `systemctl` actions needed in a Pi-specific sudoers file;
do not grant `NOPASSWD: ALL`.

## One-time Pi setup

On each Pi, enable the SSH service:

```bash
sudo systemctl enable --now ssh
```

Create a dedicated local SSH key on the Windows host:

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519_rpi_mcp"
```

Install its public key on each Pi, replacing `your-pi-user` with the account
created through Raspberry Pi Imager:

```powershell
Get-Content "$env:USERPROFILE\.ssh\id_ed25519_rpi_mcp.pub" | ssh your-pi-user@192.168.0.146 "umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
Get-Content "$env:USERPROFILE\.ssh\id_ed25519_rpi_mcp.pub" | ssh your-pi-user@192.168.0.136 "umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
```

Before accepting either SSH host key, compare the fingerprint shown on the Pi
console with:

```bash
ssh-keygen -l -f /etc/ssh/ssh_host_ed25519_key.pub
```

After verifying it, add the host key from Windows with one interactive SSH
connection. The server will then require that key to remain unchanged:

```powershell
ssh -o StrictHostKeyChecking=accept-new your-pi-user@192.168.0.146 true
ssh -o StrictHostKeyChecking=accept-new your-pi-user@192.168.0.136 true
```

For service control, edit a file through `sudo visudo -f
/etc/sudoers.d/rpi-mcp` on each Pi. Substitute only the service units you want
the MCP server to operate:

```sudoers
your-pi-user ALL=(root) NOPASSWD: /usr/bin/systemctl start mixto-api.service, /usr/bin/systemctl stop mixto-api.service, /usr/bin/systemctl restart mixto-api.service
```

Power operations require similarly scoped entries for `/usr/bin/systemctl
reboot` and `/usr/bin/systemctl shutdown` (or the appropriate local
`systemctl` path). Confirm the executable path first with `command -v
systemctl`.

## Configure and test

For source-checkout development, copy the local template from the repository
root:

```powershell
Copy-Item .env.example .env
```

Set `RPI_MCP_RPI5_HOST`, `RPI_MCP_RPI5_SSH_USER`, and
`RPI_MCP_RPI5_SSH_PORT` plus the corresponding `RPI_MCP_RPI3_*` values. When
not using the default SSH agent/key lookup, also set the dedicated
identity-file path. The `.env` file is ignored by Git and is loaded
automatically; process environment variables take priority over it.

Install the standalone project's dependencies and run its network-free tests:

```powershell
uv sync
uv run pytest
```

Check real read-only connectivity before enabling any write capability:

```powershell
uv run python -c "from rpi_mcp.server import get_raspberry_pi_status; print(get_raspberry_pi_status('rpi5'))"
uv run python -c "from rpi_mcp.server import get_raspberry_pi_status; print(get_raspberry_pi_status('rpi3bplus'))"
```

The MCP Inspector is also available once Node.js is installed:

```powershell
uv run mcp dev src/rpi_mcp/server.py
```

## VS Code

The checked-in [MCP configuration](.vscode/mcp.json) registers a local
stdio server named `raspberry-pi-ssh` from the source checkout. To use an
installed artifact instead, replace that server's command with:

```json
{
	"type": "stdio",
	"command": "rpi-mcp"
}
```

Reload VS Code after creating or changing its configuration, then start or
debug it from the MCP Servers view. The server's output uses stderr, leaving
stdout exclusively for the MCP protocol.

## References

- [Arm learning path: Set up an MCP server on Raspberry Pi 5](https://learn.arm.com/learning-paths/cross-platform/mcp-ai-agent/mcp-server/)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [MCP transport guidance](https://py.sdk.modelcontextprotocol.io/run/)