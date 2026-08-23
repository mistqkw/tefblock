"""Обход известного визуального бага: на некоторых GPU (в т.ч. Intel iGPU)
терминал «размазывается» при перерисовке, если у окна есть прозрачность —
не важно, откуда она берётся: из background_blur/background_opacity самого
kitty, или (как выяснилось на практике) из window rule композитора вроде
Hyprland, применяющего blur к прозрачным окнам поверх настроек kitty.

kitty.conf мы не трогаем, но конкретно opacity можем форсированно занулить
через `-o` при перезапуске — окно становится непрозрачным, и композитору
больше нечего блюрить позади него, даже если blur включён на уровне самого
Hyprland, а не kitty.

Если видим, что запущены внутри kitty с такой прозрачностью, тихо
перезапускаем себя в новом окне с opacity=1 только для этой сессии —
~/.config/kitty/kitty.conf при этом не трогается и продолжает применяться
ко всем остальным окнам.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RELAUNCH_GUARD = "TEFBLOCK_CLEAN_TERM"
KITTY_CONF = Path.home() / ".config" / "kitty" / "kitty.conf"

_RELEVANT_KEYS = {"background_blur", "background_opacity"}


def _read_kitty_settings(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    """Читает background_blur/background_opacity из kitty.conf, следуя
    include (последнее значение каждого ключа побеждает — как в самом kitty)."""
    if seen is None:
        seen = set()
    path = path.expanduser()
    if path in seen or not path.exists():
        return {}
    seen.add(path)

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if not parts:
            continue
        key = parts[0]
        if key == "include" and len(parts) == 2:
            included = Path(parts[1].strip()).expanduser()
            if not included.is_absolute():
                included = path.parent / included
            values.update(_read_kitty_settings(included, seen))
        elif key in _RELEVANT_KEYS and len(parts) == 2:
            values[key] = parts[1].strip()
    return values


def _kitty_needs_opaque_relaunch() -> bool:
    settings = _read_kitty_settings(KITTY_CONF)

    blur = settings.get("background_blur")
    if blur is not None:
        try:
            if int(blur) > 0:
                return True
        except ValueError:
            pass

    opacity = settings.get("background_opacity")
    if opacity is not None:
        try:
            if float(opacity) < 1.0:
                return True
        except ValueError:
            pass

    return False


def needs_clean_relaunch() -> bool:
    if sys.platform != "linux":
        return False  # kitty — линуксовый/wayland-терминал, на Windows не встречается
    if os.environ.get(RELAUNCH_GUARD):
        return False
    running_in_kitty = os.environ.get("TERM") == "xterm-kitty" or "KITTY_WINDOW_ID" in os.environ
    if not running_in_kitty:
        return False
    if shutil.which("kitty") is None:
        return False
    return _kitty_needs_opaque_relaunch()


def relaunch_clean(argv: list[str]) -> int:
    """Открывает новое окно kitty без blur/прозрачности и передаёт туда управление."""
    print("TeFBlock: у kitty включена прозрачность/blur — открываю непрозрачное окно kitty без них…")
    env = dict(os.environ)
    env[RELAUNCH_GUARD] = "1"
    cmd = [
        "kitty",
        "--detach",
        "-o", "background_blur=0",
        "-o", "background_opacity=1",
        "-d", os.getcwd(),
        *argv,
    ]
    try:
        subprocess.Popen(cmd, env=env, start_new_session=True)
    except OSError as exc:
        print(f"Не получилось открыть чистое окно kitty: {exc}")
        return 1
    return 0
