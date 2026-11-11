"""Settings, stored as TOML in the platform config directory."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import hostplatform, tomlw


@dataclass
class Settings:
    theme: str = "dark"
    force_fetched_tools: bool = False
    platform_tools_dir: str = ""
    scrcpy_path: str = ""
    heimdall_path: str = ""
    payload_dumper_path: str = ""
    abe_path: str = ""
    cache_dir: str = ""
    battery_threshold: int = 30
    log_retention_days: int = 30
    poll_seconds: int = 2
    flashing_acknowledged: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or hostplatform.settings_file()
        s = cls()
        if path.is_file():
            try:
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                return s
            known = {f.name: f.type for f in fields(cls)}
            for key, value in raw.items():
                if key in known and isinstance(value, type(getattr(s, key))):
                    setattr(s, key, value)
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or hostplatform.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(tomlw.dumps(asdict(self)), encoding="utf-8")
        tmp.replace(path)

    def effective_cache_dir(self) -> Path:
        return Path(self.cache_dir) if self.cache_dir else hostplatform.cache_dir()
