from pathlib import Path

import pytest

from rpi_mcp.cli import write_config_template


def test_write_config_template_creates_packaged_template(tmp_path: Path) -> None:
    destination = tmp_path / "config" / ".env"

    write_config_template(destination)

    assert "RPI_MCP_RPI5_HOST=" in destination.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="Configuration already exists"):
        write_config_template(destination)