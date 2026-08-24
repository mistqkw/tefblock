"""Запуск/остановка фонового демона от имени обычного пользователя.

Демон обязан работать с правами root/администратора (правит hosts-файл),
поэтому здесь используется elevate.py. Демон запускается ровно одним
elevation-запросом и сам демонизируется изнутри (POSIX: double-fork; Windows:
изначально независимый процесс) — закрытие терминала на него после этого
не влияет.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

import psutil

from . import config, elevate
from .config import AppEntry, BlockState, Selection
from .domains import expand_to_group


def build_state(selection: Selection, preset_name: str | None = None) -> BlockState:
    sites: set[str] = set()
    for raw in selection.sites:
        sites.update(expand_to_group(raw))
    now = datetime.now()
    end = now + timedelta(minutes=max(1, selection.duration_minutes))
    return BlockState(
        active=True,
        started_at=now.isoformat(),
        end_at=end.isoformat(),
        daemon_pid=None,
        preset=preset_name,
        apps=list(selection.apps),
        sites=sorted(sites),
    )


def start_block(selection: Selection, preset_name: str | None = None) -> tuple[bool, str]:
    """Пишет state.json и поднимает демона. Возвращает (успех, сообщение)."""
    if not selection.apps and not selection.sites:
        return False, "Не выбрано ни одного приложения или сайта для блокировки."

    state = build_state(selection, preset_name)
    config.save_state(state)

    try:
        ok, err = elevate.launch_daemon(
            sys.executable,
            ["-m", "tefblock.daemon", "--config-dir", str(config.CONFIG_DIR)],
        )
    except BaseException:
        # пользователь мог прервать (Ctrl+C, закрыл терминал) прямо во время
        # ввода sudo-пароля — тогда демон никогда не получал прав, hosts не
        # трогался, но state.json уже записан как active=true и застрял бы
        # так навсегда. Подчищаем и передаём прерывание дальше.
        config.clear_state()
        raise
    if not ok:
        config.clear_state()
        return False, err or "Не удалось запустить демон с повышенными правами."

    for _ in range(25):
        time.sleep(0.2)
        fresh = config.load_state()
        if fresh.active and fresh.daemon_pid:
            return True, "Блокировка запущена."
    config.clear_state()
    return False, f"Демон не подтвердил запуск — проверьте `{config.LOG_FILE}`."


def daemon_alive(state: BlockState) -> bool:
    """Проверяет, жив ли реально процесс демона, а не просто верит state.json.

    Демон может умереть не дойдя до cleanup — убит извне (OOM killer,
    systemd, ручной kill -9). Тогда state.json навсегда остаётся в статусе
    "активна", а обычный flag-файл никто не подхватит: демона больше нет."""
    if not state.daemon_pid:
        return False
    return psutil.pid_exists(state.daemon_pid)


def _repair_needs_root() -> bool:
    """Root для починки нужен, только если демон реально успел что-то сделать
    с правами root/администратора — заблокировать hosts или забрать
    state.json себе. Если демон умер (или так и не запустился) раньше этого,
    почистить всё можно и без повторного sudo-пароля."""
    try:
        if config.HOSTS_PATH.exists():
            text = config.HOSTS_PATH.read_text(encoding="utf-8")
            if config.HOSTS_MARK_START in text:
                return True
    except OSError:
        return True
    if not config.IS_WINDOWS and config.STATE_FILE.exists():
        try:
            if config.STATE_FILE.stat().st_uid != os.getuid():
                return True
        except OSError:
            return True
    return False


def repair_stuck_state() -> tuple[bool, str]:
    """Восстанавливает hosts и сбрасывает state.json, когда демон мёртв или
    так и не запустился, а state.json всё ещё считает блокировку активной."""
    if not _repair_needs_root():
        config.clear_state()
        return True, "Демон так и не успел запуститься — состояние сброшено (hosts не был тронут, sudo не понадобился)."
    ok, err = elevate.run_elevated_and_wait(
        sys.executable,
        ["-m", "tefblock.daemon", "--config-dir", str(config.CONFIG_DIR), "--repair"],
    )
    if not ok:
        return False, err or "Не удалось получить права для восстановления."
    return True, "Демон был аварийно завершён — hosts восстановлен, состояние сброшено."


def request_stop() -> tuple[bool, str]:
    state = config.load_state()
    if not state.active:
        return False, "Активной блокировки нет."

    if not daemon_alive(state):
        return repair_stuck_state()

    ok, err = elevate.run_elevated_and_wait(
        sys.executable,
        ["-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(config.STOP_FLAG_FILE)],
    )
    if not ok:
        return False, err or "Не удалось получить права для остановки."

    for _ in range(25):
        time.sleep(0.2)
        fresh = config.load_state()
        if not fresh.active:
            return True, "Блокировка снята."
        if not daemon_alive(fresh):
            return repair_stuck_state()
    return False, "Флаг остановки создан, но демон ещё не отреагировал — подождите немного."
