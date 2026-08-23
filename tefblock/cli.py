from __future__ import annotations

import argparse
import sys

from . import config, runner
from .config import load_presets, load_state


def _print_status() -> int:
    state = load_state()
    if not state.active:
        print("Блокировка не активна.")
        return 0
    if not runner.daemon_alive(state):
        print("Блокировка помечена как активна, но процесс демона не найден — похоже, он аварийно завершился.")
        print("Сайты/приложения могут остаться заблокированными, пока это не починится.")
        print("Выполни `block --stop` — он обнаружит это и всё восстановит сам.")
        return 0
    preset = f" (пресет: {state.preset})" if state.preset else ""
    print(f"Блокировка активна{preset}.")
    if state.apps:
        print("Приложения: " + ", ".join(a.name for a in state.apps))
    if state.sites:
        print("Сайты: " + ", ".join(state.sites))
    print()
    from . import textmode

    textmode.watch_countdown()
    return 0


def _stop_flow() -> int:
    state = load_state()
    if not state.active:
        print("Блокировка не активна — останавливать нечего.")
        return 0
    print("Ты уверен, что хочешь снять блокировку раньше времени?")
    print('Введи ровно "снять блокировку", чтобы подтвердить:')
    try:
        answer = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nОтменено.")
        return 1
    if answer != "снять блокировку":
        print("Не совпало — блокировка остаётся активной.")
        return 1
    ok, message = runner.request_stop()
    print(message)
    return 0 if ok else 1


def _list_presets() -> int:
    presets = load_presets()
    if not presets:
        print("Пресетов пока нет. Создай их через `block` (в конце спросит имя для сохранения).")
        return 0
    for name, sel in presets.items():
        apps = ", ".join(a.name for a in sel.apps) or "—"
        sites = ", ".join(sel.sites) or "—"
        print(f"{name}: {sel.duration_minutes} мин | приложения: {apps} | сайты: {sites}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="block", description="TeFBlock — блокировщик отвлечений")
    parser.add_argument("preset", nargs="?", help="имя пресета для мгновенного запуска (например work)")
    parser.add_argument("--status", action="store_true", help="показать статус текущей блокировки")
    parser.add_argument("--stop", action="store_true", help="досрочно снять блокировку (нужен sudo)")
    parser.add_argument("--list-presets", action="store_true", help="показать сохранённые пресеты")
    parser.add_argument(
        "--tui", action="store_true",
        help="открыть полноэкранный TUI (Textual) — сейчас это и есть поведение по умолчанию",
    )
    parser.add_argument(
        "--text", action="store_true",
        help="обычный текстовый диалог без альтернативного экрана, вместо TUI",
    )
    args = parser.parse_args(argv)

    config.ensure_config_dir()

    if args.status:
        return _print_status()
    if args.stop:
        return _stop_flow()
    if args.list_presets:
        return _list_presets()

    from . import termfix

    if termfix.needs_clean_relaunch():
        return termfix.relaunch_clean(sys.argv)

    if args.text:
        from . import textmode

        return textmode.run_wizard(initial_preset=args.preset)

    from .tui.app import run_app  # локальный импорт: textual грузится только когда реально нужен TUI

    if args.preset:
        presets = load_presets()
        if args.preset not in presets:
            print(f'Пресета "{args.preset}" нет. Доступные: {", ".join(presets) or "нет ни одного"}')
            return 1
        return run_app(initial_preset=args.preset)
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
