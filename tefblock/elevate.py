"""Кроссплатформенный запуск процессов с правами root/администратора.

POSIX (Linux/macOS): `sudo`.
Windows: UAC-элевация через `ShellExecuteEx` с verb "runas" (пакет pywin32).

Оба пути дают одинаковый интерфейс: launch_daemon() запускает и не ждёт
(демон либо сам демонизируется через fork на POSIX, либо изначально
независим от родителя на Windows), run_elevated_and_wait() — синхронно
исполняет короткую команду и ждёт её завершения (используется для
`block --stop`).
"""
from __future__ import annotations

import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"


def launch_daemon(python_exe: str, module_args: list[str]) -> tuple[bool, str]:
    """Запускает демон с повышенными правами, не дожидаясь его завершения."""
    if IS_WINDOWS:
        return _windows_run(python_exe, module_args, wait=False)
    try:
        result = subprocess.run(["sudo", python_exe, *module_args])
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, "sudo отказал в правах, или процесс сразу завершился с ошибкой."
    return True, ""


def run_elevated_and_wait(python_exe: str, module_args: list[str]) -> tuple[bool, str]:
    """Синхронно выполняет короткую команду с повышенными правами."""
    if IS_WINDOWS:
        return _windows_run(python_exe, module_args, wait=True)
    try:
        result = subprocess.run(["sudo", python_exe, *module_args])
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, "sudo отказал в правах."
    return True, ""


def _windows_python_w(python_exe: str) -> str:
    """pythonw.exe не создаёт консольное окно — предпочитаем его, если есть."""
    from pathlib import Path

    p = Path(python_exe)
    candidate = p.with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else python_exe


def _windows_run(python_exe: str, module_args: list[str], wait: bool) -> tuple[bool, str]:
    try:
        import win32con
        import win32event
        import win32process
        from win32com.shell import shellcon
        from win32com.shell.shell import ShellExecuteEx
    except ImportError:
        return False, "Для Windows нужен пакет pywin32: pip install pywin32"

    exe = _windows_python_w(python_exe) if not wait else python_exe
    params = subprocess.list2cmdline(module_args)
    mask = shellcon.SEE_MASK_NOCLOSEPROCESS if wait else 0

    try:
        info = ShellExecuteEx(
            fMask=mask,
            lpVerb="runas",
            lpFile=exe,
            lpParameters=params,
            nShow=win32con.SW_HIDE,
        )
    except Exception as exc:  # pywintypes.error, в т.ч. при отмене UAC-запроса
        return False, f"Не удалось получить права администратора: {exc}"

    if wait:
        handle = info["hProcess"]
        win32event.WaitForSingleObject(handle, win32event.INFINITE)
        code = win32process.GetExitCodeProcess(handle)
        if code != 0:
            return False, f"Команда завершилась с кодом {code}."
    return True, ""
