"""Обход известного визуального бага: kitty с включённым background_blur
на некоторых GPU (в т.ч. Intel iGPU) «размазывает» экран при перерисовке
терминала — не важно, TUI это или обычный текст.

Если видим, что запущены внутри такого kitty, тихо перезапускаем себя в
новом окне kitty с отключённым blur только для этой сессии — обычный
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


def _kitty_blur_enabled() -> bool:
    if not KITTY_CONF.exists():
        return False
    try:
        lines = KITTY_CONF.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[0] == "background_blur":
            try:
                return int(parts[1]) > 0
            except ValueError:
                return False
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
    return _kitty_blur_enabled()


def relaunch_clean(argv: list[str]) -> int:
    """Открывает новое окно kitty без blur/прозрачности и передаёт туда управление."""
    print("TeFBlock: у kitty включён background_blur — открываю чистое окно kitty без него…")
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
