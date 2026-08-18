"""Установка команд-алиасов для пресетов (например `work`, `learn`) в shell.

POSIX: функция вида `work() { command block "work" "$@"; }` в ~/.bashrc и
~/.zshrc. Windows: функция `function work { & block "work" @args }` в
профиле PowerShell ($PROFILE, для Windows PowerShell 5 и PowerShell 7).
Обрамляется маркерами, чтобы можно было чисто удалить/обновить.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import IS_WINDOWS

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _posix_rc_files() -> list[Path]:
    return [Path.home() / ".bashrc", Path.home() / ".zshrc"]


def _windows_profile_files() -> list[Path]:
    docs = Path.home() / "Documents"
    return [
        docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",  # Windows PowerShell 5.1
        docs / "PowerShell" / "Microsoft.PowerShell_profile.ps1",  # PowerShell 7+
    ]


RC_FILES = _windows_profile_files() if IS_WINDOWS else _posix_rc_files()


def is_valid_alias_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def existing_command_conflict(name: str) -> str | None:
    """Если команда с таким именем уже существует в системе — вернуть путь к ней."""
    return shutil.which(name)


def _marker(name: str) -> tuple[str, str]:
    return (f"# >>> tefblock:{name} >>>", f"# <<< tefblock:{name} <<<")


def _function_block(name: str) -> str:
    start, end = _marker(name)
    if IS_WINDOWS:
        body = f'function {name} {{ & block "{name}" @args }}'
    else:
        body = f'{name}() {{ command block "{name}" "$@"; }}'
    return f"{start}\n{body}\n{end}\n"


def _strip_block(text: str, name: str) -> str:
    start, end = _marker(name)
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    return pattern.sub("", text)


def install_alias(name: str) -> list[Path]:
    """Добавляет функцию-алиас во все найденные rc/profile-файлы. Возвращает изменённые файлы."""
    block = _function_block(name)
    changed: list[Path] = []
    for rc in RC_FILES:
        if not rc.exists():
            # На Windows создаём сам файл профиля, если уже есть папка этой
            # версии PowerShell (то есть ей хоть раз пользовались) — но не
            # плодим директории для версии, которой никогда не было.
            if IS_WINDOWS and rc.parent.is_dir():
                rc.write_text("", encoding="utf-8")
            else:
                continue
        text = rc.read_text(encoding="utf-8")
        text = _strip_block(text, name)
        if text and not text.endswith("\n"):
            text += "\n"
        text += block
        rc.write_text(text, encoding="utf-8")
        changed.append(rc)
    return changed


def uninstall_alias(name: str) -> list[Path]:
    changed: list[Path] = []
    for rc in RC_FILES:
        if not rc.exists():
            continue
        text = rc.read_text(encoding="utf-8")
        new_text = _strip_block(text, name)
        if new_text != text:
            rc.write_text(new_text, encoding="utf-8")
            changed.append(rc)
    return changed


def list_installed_aliases() -> set[str]:
    names: set[str] = set()
    pattern = re.compile(r"# >>> tefblock:(\S+) >>>")
    for rc in RC_FILES:
        if not rc.exists():
            continue
        text = rc.read_text(encoding="utf-8")
        names.update(pattern.findall(text))
    return names
