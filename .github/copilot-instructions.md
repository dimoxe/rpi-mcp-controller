# Raspberry Pi MCP Server

This project uses the current MCP Python SDK:

- https://py.sdk.modelcontextprotocol.io/
- https://github.com/modelcontextprotocol/python-sdk
- https://learn.arm.com/learning-paths/cross-platform/mcp-ai-agent/mcp-server/

Use `MCPServer` and stdio for new local VS Code integrations. Keep the server
LAN-local and retain the explicit command and power-action opt-ins.

The agent-facing notes for `run_command` live in `_AGENT_COMMAND_NOTES` in
`src/rpi_mcp/server.py` and are sent through both the server instructions and
the tool description. Keep them in sync with how commands actually execute:
non-interactive SSH with empty stdin, plain `sudo` (the server supplies the
password through `SUDO_ASKPASS` when `RPI_MCP_ENABLE_RUN_COMMAND_SUDO=1`), never
`sudo -S`/`-n`/`-A` or a password in the command, `&&` chaining, and a
120-second timeout.
