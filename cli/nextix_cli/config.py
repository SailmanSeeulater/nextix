"""CLI configuration: ~/.config/nextix/config.toml, overridable by environment variables.

    api_url      = "http://localhost:8000"
    token        = "..."
    default_repo = "owner/name"   # optional

Env overrides: NEXTIX_API_URL, NEXTIX_API_TOKEN, NEXTIX_REPO, NEXTIX_CONFIG_DIR.
"""

import contextlib
import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API_URL = "http://localhost:8000"


def config_dir() -> Path:
    override = os.environ.get("NEXTIX_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".config" / "nextix"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class CliConfig:
    api_url: str = DEFAULT_API_URL
    token: str = ""
    default_repo: str = ""


def load() -> CliConfig:
    cfg = CliConfig()
    path = config_path()
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        cfg.api_url = str(data.get("api_url", cfg.api_url))
        cfg.token = str(data.get("token", ""))
        cfg.default_repo = str(data.get("default_repo", ""))
    cfg.api_url = os.environ.get("NEXTIX_API_URL", cfg.api_url).rstrip("/")
    cfg.token = os.environ.get("NEXTIX_API_TOKEN", cfg.token)
    cfg.default_repo = os.environ.get("NEXTIX_REPO", cfg.default_repo)
    return cfg


def save(cfg: CliConfig) -> Path:
    """Write the config file, readable only by the current user where supported."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # JSON string escaping is valid TOML basic-string escaping for these values.
    lines = [
        f"api_url = {json.dumps(cfg.api_url)}",
        f"token = {json.dumps(cfg.token)}",
    ]
    if cfg.default_repo:
        lines.append(f"default_repo = {json.dumps(cfg.default_repo)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):  # some Windows filesystems ignore modes
        path.chmod(0o600)
    return path
