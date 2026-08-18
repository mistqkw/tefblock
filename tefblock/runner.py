"""Запуск/остановка фонового демона от имени обычного пользователя.

Демон обязан работать с правами root/администратора (правит hosts-файл),
поэтому здесь используется elevate.py. Демон запускается ровно одним
elevation-запросом и сам демонизируется изнутри (POSIX: double-fork; Windows:
изначально независимый процесс) — закрытие терминала на него после этого
не влияет.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

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

    ok, err = elevate.launch_daemon(
        sys.executable,
        ["-m", "tefblock.daemon", "--config-dir", str(config.CONFIG_DIR)],
    )
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


def request_stop() -> tuple[bool, str]:
    state = config.load_state()
    if not state.active or not state.daemon_pid:
        return False, "Активной блокировки нет."

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
    return False, "Флаг остановки создан, но демон ещё не отреагировал — подождите немного."
