"""Пути и хранение конфигурации TeFBlock.

Linux/macOS: ~/.config/tefblock, /etc/hosts.
Windows: %APPDATA%\\TeFBlock, C:\\Windows\\System32\\drivers\\etc\\hosts.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform == "win32"


def _default_config_dir() -> Path:
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "TeFBlock"
    return Path.home() / ".config" / "tefblock"


def _default_hosts_path() -> Path:
    if IS_WINDOWS:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(system_root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


CONFIG_DIR = Path(os.environ.get("TEFBLOCK_CONFIG_DIR", _default_config_dir()))
SELECTION_FILE = CONFIG_DIR / "selection.json"
PRESETS_FILE = CONFIG_DIR / "presets.json"
STATE_FILE = CONFIG_DIR / "state.json"
STOP_FLAG_FILE = CONFIG_DIR / "stop.flag"
LOG_FILE = CONFIG_DIR / "daemon.log"

HOSTS_PATH = Path(os.environ.get("TEFBLOCK_HOSTS_PATH", _default_hosts_path()))
HOSTS_MARK_START = "# >>> TEFBLOCK BLOCK START >>>"
HOSTS_MARK_END = "# <<< TEFBLOCK BLOCK END <<<"


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    ensure_config_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@dataclass
class AppEntry:
    app_id: str
    name: str
    match_tokens: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"app_id": self.app_id, "name": self.name, "match_tokens": self.match_tokens}

    @staticmethod
    def from_dict(d: dict) -> "AppEntry":
        return AppEntry(app_id=d["app_id"], name=d["name"], match_tokens=d.get("match_tokens", []))


@dataclass
class Selection:
    apps: list[AppEntry] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    duration_minutes: int = 20

    def to_dict(self) -> dict:
        return {
            "apps": [a.to_dict() for a in self.apps],
            "sites": self.sites,
            "duration_minutes": self.duration_minutes,
        }

    @staticmethod
    def from_dict(d: dict) -> "Selection":
        return Selection(
            apps=[AppEntry.from_dict(a) for a in d.get("apps", [])],
            sites=d.get("sites", []),
            duration_minutes=d.get("duration_minutes", 20),
        )


def load_selection() -> Selection:
    return Selection.from_dict(_read_json(SELECTION_FILE, {}))


def save_selection(sel: Selection) -> None:
    _write_json(SELECTION_FILE, sel.to_dict())


def load_presets() -> dict[str, Selection]:
    raw = _read_json(PRESETS_FILE, {})
    return {name: Selection.from_dict(val) for name, val in raw.items()}


def save_presets(presets: dict[str, Selection]) -> None:
    _write_json(PRESETS_FILE, {name: sel.to_dict() for name, sel in presets.items()})


def save_preset(name: str, sel: Selection) -> None:
    presets = load_presets()
    presets[name] = sel
    save_presets(presets)


def delete_preset(name: str) -> None:
    presets = load_presets()
    presets.pop(name, None)
    save_presets(presets)


@dataclass
class BlockState:
    active: bool = False
    started_at: str | None = None
    end_at: str | None = None
    daemon_pid: int | None = None
    preset: str | None = None
    apps: list[AppEntry] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "started_at": self.started_at,
            "end_at": self.end_at,
            "daemon_pid": self.daemon_pid,
            "preset": self.preset,
            "apps": [a.to_dict() for a in self.apps],
            "sites": self.sites,
        }

    @staticmethod
    def from_dict(d: dict) -> "BlockState":
        return BlockState(
            active=d.get("active", False),
            started_at=d.get("started_at"),
            end_at=d.get("end_at"),
            daemon_pid=d.get("daemon_pid"),
            preset=d.get("preset"),
            apps=[AppEntry.from_dict(a) for a in d.get("apps", [])],
            sites=d.get("sites", []),
        )

    @property
    def end_datetime(self) -> datetime | None:
        return datetime.fromisoformat(self.end_at) if self.end_at else None

    @property
    def seconds_left(self) -> float:
        end = self.end_datetime
        if end is None:
            return 0.0
        return max(0.0, (end - datetime.now()).total_seconds())


def load_state() -> BlockState:
    return BlockState.from_dict(_read_json(STATE_FILE, {}))


def save_state(state: BlockState) -> None:
    _write_json(STATE_FILE, state.to_dict())


def clear_state() -> None:
    save_state(BlockState())
