"""Фоновый процесс блокировки. Запускается с правами root/администратора и
живёт отдельно от терминала/TUI, поэтому блокировка не снимается при
закрытии окна.

Логика самозащиты (Linux/macOS):
  * state.json на время блокировки становится root:root (0644) — обычный
    пользователь может его читать (для статуса), но не может отредактировать
    и снять блокировку в обход таймера.
  * SIGTERM/SIGINT/SIGHUP игнорируются — случайный `kill <pid>` не поможет.

Остановка раньше времени (все платформы): `block --stop` спрашивает
подтверждение и права root/администратора, чтобы создать stop.flag —
демон проверяет его каждую итерацию цикла.
"""
from __future__ import annotations

import os
import sys


def _apply_config_dir_override() -> None:
    """Читаем --config-dir из argv ДО импорта config.

    Демон почти всегда запускается через `sudo`, а sudo по умолчанию (env_reset)
    меняет $HOME на домашний каталог root. Если бы config.py искал каталог сам
    через Path.home(), демон от root смотрел бы в /root/.config/tefblock вместо
    каталога того, кто реально запускал блокировку. Поэтому runner.py всегда
    передаёт исходный каталог явным аргументом — в отличие от переменных
    окружения, аргументы командной строки sudo никогда не обрезает.
    """
    for i, arg in enumerate(sys.argv):
        if arg == "--config-dir" and i + 1 < len(sys.argv):
            os.environ["TEFBLOCK_CONFIG_DIR"] = sys.argv[i + 1]
            return
        if arg.startswith("--config-dir="):
            os.environ["TEFBLOCK_CONFIG_DIR"] = arg.split("=", 1)[1]
            return


_apply_config_dir_override()
_REPAIR_MODE = "--repair" in sys.argv

import time
from datetime import datetime
from pathlib import Path

import psutil

from . import config
from .config import AppEntry, BlockState

POLL_INTERVAL_SECONDS = 2.0


def _log(message: str) -> None:
    config.ensure_config_dir()
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n"
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _is_admin() -> bool:
    if config.IS_WINDOWS:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _install_signal_handlers() -> None:
    """SIGTERM/SIGINT/SIGHUP игнорируются — только POSIX, на Windows таких
    сигналов в привычном виде нет, и `os.kill` там всё равно не работает так,
    чтобы это имело смысл."""
    if config.IS_WINDOWS:
        return
    import signal

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)


def apply_hosts_block(domains: list[str]) -> None:
    lines = _read_hosts_lines()
    lines = _strip_marked_block(lines)
    if lines and lines[-1].strip() != "":
        lines.append("\n")
    lines.append(config.HOSTS_MARK_START + "\n")
    for domain in domains:
        lines.append(f"127.0.0.1 {domain}\n")
        lines.append(f"::1 {domain}\n")
    lines.append(config.HOSTS_MARK_END + "\n")
    config.HOSTS_PATH.write_text("".join(lines), encoding="utf-8")


def remove_hosts_block() -> None:
    lines = _read_hosts_lines()
    new_lines = _strip_marked_block(lines)
    if new_lines != lines:
        config.HOSTS_PATH.write_text("".join(new_lines), encoding="utf-8")


def _read_hosts_lines() -> list[str]:
    if not config.HOSTS_PATH.exists():
        return []
    return config.HOSTS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)


def _strip_marked_block(lines: list[str]) -> list[str]:
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == config.HOSTS_MARK_START:
            inside = True
            continue
        if stripped == config.HOSTS_MARK_END:
            inside = False
            continue
        if inside:
            continue
        out.append(line)
    return out


_SKIP_TOKENS = {"", "sh", "bash", "env", "python", "python3"}


def kill_blocked_apps(apps: list[AppEntry]) -> int:
    exact_tokens: set[str] = set()
    substring_tokens: set[str] = set()
    for a in apps:
        for t in a.match_tokens:
            t = t.lower()
            if t in _SKIP_TOKENS:
                continue
            if t.startswith("arg:"):
                substring_tokens.add(t[len("arg:"):])
            else:
                exact_tokens.add(t)
    if not exact_tokens and not substring_tokens:
        return 0
    killed = 0
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            exe_base = os.path.basename(info.get("exe") or "").lower()
            cmdline = [str(c).lower() for c in (info.get("cmdline") or [])]
            args_base = {os.path.basename(c) for c in cmdline}
            cmdline_joined = " ".join(cmdline)
            hit = (
                name in exact_tokens
                or exe_base in exact_tokens
                or bool(exact_tokens & set(cmdline))
                or bool(exact_tokens & args_base)
                or any(sub in cmdline_joined for sub in substring_tokens)
            )
            if not hit:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except psutil.TimeoutExpired:
                proc.kill()
            killed += 1
            _log(f"остановлено приложение: pid={info.get('pid')} name={name or exe_base}")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return killed


def _take_root_ownership(path: Path) -> tuple[int, int] | None:
    """Только POSIX: делает state.json root:root 0644, чтобы обычный
    пользователь не мог отредактировать его в обход таймера. На Windows
    эквивалента через ACL пока нет — файл просто остаётся как есть."""
    if config.IS_WINDOWS:
        return None
    st = path.stat()
    orig = (st.st_uid, st.st_gid)
    os.chown(path, 0, 0)
    os.chmod(path, 0o644)
    return orig


def _release_ownership(path: Path, owner: tuple[int, int] | None) -> None:
    if config.IS_WINDOWS or not path.exists():
        return
    if owner is None:
        # используется из repair(): владельца никто не запоминал (у нас нет
        # живого демона, из которого он был бы захвачен), поэтому берём его
        # из SUDO_UID/SUDO_GID — их sudo сам подставляет для вызвавшего пользователя
        sudo_uid = os.environ.get("SUDO_UID")
        sudo_gid = os.environ.get("SUDO_GID")
        if sudo_uid is None or sudo_gid is None:
            _log("не удалось определить исходного владельца state.json (нет SUDO_UID/SUDO_GID)")
            return
        owner = (int(sudo_uid), int(sudo_gid))
    try:
        os.chown(path, owner[0], owner[1])
        os.chmod(path, 0o600)
    except OSError as exc:
        _log(f"не удалось вернуть владельца state.json: {exc}")


def _daemonize() -> None:
    """Только POSIX. Классический double-fork: процесс отсоединяется от
    вызвавшего sudo/TUI и от управляющего терминала (setsid), родители сразу
    завершаются.

    Это делается один раз, ВНУТРИ уже полученных прав root — благодаря этому
    достаточно ровно одного вызова `sudo`. Отдельный второй `sudo`-вызов без
    tty (как было раньше) иногда молча отказывал в правах — и демон никогда
    не запускался, а `state.json` так и оставался в статусе "активна" навечно.

    На Windows не нужно: процесс, запущенный через ShellExecuteEx без
    ожидания, изначально независим от родителя.
    """
    if config.IS_WINDOWS:
        return
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull_fd = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull_fd, 0)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)


def _stop_requested() -> bool:
    return config.STOP_FLAG_FILE.exists()


def _clear_stop_flag() -> None:
    try:
        config.STOP_FLAG_FILE.unlink()
    except OSError:
        pass


def repair() -> int:
    """Чинит зависшее состояние: демон умер, не дойдя до cleanup (убит извне —
    OOM killer, systemd, ручной kill -9 и т.п.). В этом случае state.json
    навсегда остаётся root-owned с active=true, а /etc/hosts — заблокированным,
    и обычный flag-файл никто не подхватит, потому что демона больше нет.
    Восстанавливает hosts, сбрасывает state.json и возвращает его пользователю."""
    if not _is_admin():
        print("repair должен запускаться с правами root/администратора", file=sys.stderr)
        return 1
    remove_hosts_block()
    config.clear_state()
    _clear_stop_flag()
    _release_ownership(config.STATE_FILE, None)
    _log("восстановление зависшего состояния (--repair): hosts и state.json сброшены")
    print("Готово: hosts восстановлен, state.json сброшен.")
    return 0


def run() -> int:
    if not _is_admin():
        print("daemon должен запускаться с правами root/администратора", file=sys.stderr)
        return 1

    state = config.load_state()
    if not state.active or not state.end_at:
        _log("нет активной блокировки при запуске демона — выхожу")
        return 0

    _daemonize()
    _clear_stop_flag()

    owner = _take_root_ownership(config.STATE_FILE)
    state.daemon_pid = os.getpid()
    config.save_state(state)

    all_domains = sorted(set(state.sites))
    apply_hosts_block(all_domains)
    _install_signal_handlers()
    _log(f"блокировка запущена: apps={[a.name for a in state.apps]} sites={all_domains} до {state.end_at}")

    try:
        while True:
            if _stop_requested():
                _log("получен запрос на досрочную остановку (block --stop)")
                break
            state = config.load_state()
            if state.seconds_left <= 0:
                _log("время блокировки истекло")
                break
            if state.apps:
                kill_blocked_apps(state.apps)
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        remove_hosts_block()
        config.clear_state()
        _clear_stop_flag()
        _release_ownership(config.STATE_FILE, owner)
        _log("блокировка снята, hosts восстановлен")

    return 0


if __name__ == "__main__":
    sys.exit(repair() if _REPAIR_MODE else run())
