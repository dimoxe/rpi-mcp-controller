"""Command-line entry point for the installable Raspberry Pi MCP tool."""

from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path

from .config import config_file_path
from .server import mcp


def main() -> None:
    """Run the MCP server or manage its operator-owned configuration file."""

    parser = argparse.ArgumentParser(prog="rpi-mcp")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("config-path", help="Print the active configuration file path.")
    init_parser = subcommands.add_parser(
        "init-config", help="Create a configuration template at the default path."
    )
    init_parser.add_argument("--force", action="store_true", help="Replace an existing file.")
    init_parser.add_argument("--path", type=Path, help="Write the template to this path instead.")
    args = parser.parse_args()

    if args.command == "config-path":
        print(config_file_path())
        return
    if args.command == "init-config":
        destination = args.path or config_file_path()
        try:
            write_config_template(destination, force=args.force)
        except FileExistsError as error:
            parser.error(str(error))
        print(f"Created configuration template: {destination}")
        return

    mcp.run()


def write_config_template(path: Path, *, force: bool = False) -> None:
    """Copy the packaged configuration template without overwriting by default."""

    destination = path.expanduser()
    if destination.exists() and not force:
        raise FileExistsError(f"Configuration already exists at {destination}. Use --force to replace it.")
    template = files("rpi_mcp").joinpath("templates").joinpath("default.env")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")