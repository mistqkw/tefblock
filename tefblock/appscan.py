"""Поиск установленных приложений: .desktop-файлы (Linux) или ярлыки Start
Menu (Windows)."""
from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

from .config import AppEntry, IS_WINDOWS

DESKTOP_DIRS = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path.home() / ".local/share/applications",
    Path.home() / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
]

def _windows_start_menu_dirs() -> list[Path]:
    dirs = []
    for env_var in ("APPDATA", "ProgramData"):
        base = os.environ.get(env_var)
        if base:
            dirs.append(Path(base) / "Microsoft/Windows/Start Menu/Programs")
    return dirs

_PLACEHOLDER_RE = re.compile(r"%[a-zA-Z]")

# Веб-приложения/PWA (Photopea, YouTube Music и т.п.) запускаются через общий
# бинарник браузера с флагом --app-id=<id> или --app=<url>. Если добавлять сам
# бинарник в match_tokens как есть, блокировка одного такого приложения будет
# убивать вообще весь браузер — у него ведь то же имя процесса. Поэтому для
# таких случаев ищем конкретный --app-id/--app и матчим по нему отдельно
# (см. "arg:" префикс и его обработку в daemon.kill_blocked_apps).
_GENERIC_BROWSER_BINARIES = {
    "chromium", "chromium-browser", "chrome", "google-chrome",
    "google-chrome-stable", "google-chrome-beta", "brave", "brave-browser",
    "microsoft-edge", "microsoft-edge-stable", "msedge", "vivaldi",
    "vivaldi-stable", "opera",
}


def _first_exec_token(exec_line: str) -> str:
    exec_line = _PLACEHOLDER_RE.sub("", exec_line).strip()
    if not exec_line:
        return ""
    # убираем обёртки типа env, flatpak run, snap run
    parts = exec_line.split()
    return parts[0] if parts else ""


def _extract_app_id_flag(parts: list[str]) -> str | None:
    for p in parts:
        if p.startswith("--app-id="):
            return p.split("=", 1)[1]
        if p.startswith("--app="):
            return p.split("=", 1)[1]
    return None


def _match_tokens_for(exec_line: str, desktop_id: str, wm_class: str) -> list[str]:
    tokens: set[str] = set()
    parts = [p for p in exec_line.split() if p and not p.startswith("%")]
    if parts:
        first = os.path.basename(parts[0])
        app_id = _extract_app_id_flag(parts[1:])
        if first in _GENERIC_BROWSER_BINARIES and app_id:
            # не добавляем сам "chromium"/"google-chrome" — слишком общий,
            # матчим только по конкретному --app-id/--app этого веб-приложения
            tokens.add(f"arg:{app_id}")
        else:
            tokens.add(first)
        # flatpak run org.telegram.desktop -> тоже ищем app id в cmdline
        if first in ("flatpak",) and len(parts) >= 3 and parts[1] == "run":
            tokens.add(parts[2])
        if first in ("snap",) and len(parts) >= 2:
            tokens.add(parts[1])
    stem = desktop_id.removesuffix(".desktop")
    if stem:
        tokens.add(stem)
    if wm_class:
        tokens.add(wm_class)
    return sorted(t for t in tokens if t)


def scan_installed_apps() -> list[AppEntry]:
    """Возвращает отсортированный список приложений, видимых в меню."""
    if IS_WINDOWS:
        apps = _scan_windows_apps()
    else:
        apps = _scan_linux_apps()
    apps.sort(key=lambda a: a.name.lower())
    return apps


def _scan_linux_apps() -> list[AppEntry]:
    seen_ids: set[str] = set()
    apps: list[AppEntry] = []

    for base_dir in DESKTOP_DIRS:
        if not base_dir.is_dir():
            continue
        for desktop_file in sorted(base_dir.rglob("*.desktop")):
            desktop_id = desktop_file.name
            if desktop_id in seen_ids:
                continue
            entry = _parse_desktop_file(desktop_file, desktop_id)
            if entry is None:
                continue
            seen_ids.add(desktop_id)
            apps.append(entry)
    return apps


def _scan_windows_apps() -> list[AppEntry]:
    try:
        import win32com.client
    except ImportError:
        return []

    shell = win32com.client.Dispatch("WScript.Shell")
    seen_ids: set[str] = set()
    apps: list[AppEntry] = []

    for base_dir in _windows_start_menu_dirs():
        if not base_dir.is_dir():
            continue
        for lnk_file in sorted(base_dir.rglob("*.lnk")):
            desktop_id = str(lnk_file.relative_to(base_dir))
            if desktop_id in seen_ids:
                continue
            entry = _parse_windows_shortcut(shell, lnk_file, desktop_id)
            if entry is None:
                continue
            seen_ids.add(desktop_id)
            apps.append(entry)
    return apps


def _parse_windows_shortcut(shell, path: Path, desktop_id: str) -> AppEntry | None:
    try:
        shortcut = shell.CreateShortCut(str(path))
        target = shortcut.TargetPath
        arguments = shortcut.Arguments or ""
    except Exception:
        return None
    if not target:
        return None

    # используем ntpath явно (не os.path) — путь всегда в стиле Windows,
    # независимо от того, на какой ОС в итоге исполняется этот код
    import ntpath

    target_name = ntpath.basename(target)
    target_stem = ntpath.splitext(target_name)[0]
    if not target_name:
        return None

    # веб-приложения Chrome/Edge/Brave (--app-id=X) запускаются через общий
    # exe браузера — та же логика, что и для .desktop на Linux (см. вверху)
    app_id = _extract_app_id_flag(arguments.split())
    tokens: set[str] = set()
    if target_stem.lower() in _GENERIC_BROWSER_BINARIES and app_id:
        tokens.add(f"arg:{app_id}")
    else:
        tokens.add(target_name)
        if target_stem:
            tokens.add(target_stem)

    if not tokens:
        return None

    return AppEntry(app_id=desktop_id, name=path.stem, match_tokens=sorted(tokens))


def _parse_desktop_file(path: Path, desktop_id: str) -> AppEntry | None:
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None

    if "Desktop Entry" not in parser:
        return None
    section = parser["Desktop Entry"]

    if section.get("Type", "Application") != "Application":
        return None
    if section.get("NoDisplay", "false").lower() == "true":
        return None
    if section.get("Hidden", "false").lower() == "true":
        return None

    exec_line = section.get("Exec", "")
    name = section.get("Name", desktop_id.removesuffix(".desktop"))
    if not exec_line:
        return None

    wm_class = section.get("StartupWMClass", "")
    tokens = _match_tokens_for(exec_line, desktop_id, wm_class)
    if not tokens:
        return None

    return AppEntry(app_id=desktop_id, name=name, match_tokens=tokens)
