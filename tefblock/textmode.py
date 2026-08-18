"""Обычный текстовый интерфейс: без альтернативного экрана и полноэкранных
перерисовок — безопасно для прозрачных/blur-терминалов. Пишет как типичная
консольная утилита: вопрос — ответ — следующая строка, ничего не «мигает».

Красивый полноэкранный TUI (`tefblock.tui`) никуда не делся — он доступен
через `block --tui`.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from . import appscan, config, installer, runner
from .config import AppEntry, Selection
from .domains import normalize_site_input

console = Console()

MAX_SEARCH_RESULTS = 15


def _header() -> None:
    console.print(Panel.fit("[bold cyan]TeFBlock[/bold cyan]", border_style="cyan"))


def pick_apps() -> list[AppEntry]:
    all_apps = appscan.scan_installed_apps()
    chosen: dict[str, AppEntry] = {}

    console.print("\n[bold]Шаг 1 из 3 — Приложения[/bold]")
    console.print("[dim]Введи часть названия, чтобы найти. Пусто — перейти дальше.[/dim]")

    while True:
        query = Prompt.ask("Поиск приложения", default="").strip()
        if not query:
            break
        matches = [a for a in all_apps if query.lower() in a.name.lower()][:MAX_SEARCH_RESULTS]
        if not matches:
            console.print("[yellow]Ничего не нашлось[/yellow]")
            continue

        table = Table(show_header=False, box=None, pad_edge=False)
        for i, app in enumerate(matches, 1):
            mark = "[green]✓[/green]" if app.app_id in chosen else " "
            table.add_row(f"{mark} {i}.", app.name)
        console.print(table)

        raw = Prompt.ask("Номера через запятую (пусто — не добавлять)", default="")
        added: list[str] = []
        for tok in raw.replace(",", " ").split():
            if tok.isdigit() and 1 <= int(tok) <= len(matches):
                app = matches[int(tok) - 1]
                chosen[app.app_id] = app
                added.append(app.name)
        if added:
            console.print("[green]Добавлено:[/green] " + ", ".join(added))
        if chosen:
            console.print("[dim]Всего выбрано:[/dim] " + ", ".join(a.name for a in chosen.values()))

    return list(chosen.values())


def pick_sites() -> list[str]:
    sites: list[str] = []

    console.print("\n[bold]Шаг 2 из 3 — Сайты[/bold]")
    console.print("[dim]Домен или ссылка (напр. https://youtu.be/xxx). Пусто — перейти дальше.[/dim]")

    while True:
        raw = Prompt.ask("Сайт или ссылка", default="").strip()
        if not raw:
            break
        canonical, domains = normalize_site_input(raw)
        if not canonical:
            console.print("[yellow]Не удалось распознать[/yellow]")
            continue
        if canonical not in sites:
            sites.append(canonical)
        extra = [d for d in domains if d != canonical]
        suffix = f" [dim](+{', '.join(extra)})[/dim]" if extra else ""
        console.print(f"[green]Добавлено:[/green] {canonical}{suffix}")

    return sites


def pick_duration() -> int:
    console.print("\n[bold]Шаг 3 из 3 — Таймер[/bold]")
    console.print("  [cyan]1[/cyan]) 1 минута    [cyan]2[/cyan]) 20 минут    [cyan]3[/cyan]) 1 час    [cyan]4[/cyan]) своё число минут")
    choice = Prompt.ask("Выбор", choices=["1", "2", "3", "4"], default="2")
    if choice == "1":
        return 1
    if choice == "2":
        return 20
    if choice == "3":
        return 60
    while True:
        minutes = IntPrompt.ask("Сколько минут")
        if minutes > 0:
            return minutes
        console.print("[yellow]Нужно положительное число[/yellow]")


def maybe_save_preset(selection: Selection) -> str | None:
    name = Prompt.ask('\nСохранить это как пресет? Имя (напр. "work"), пусто — пропустить', default="").strip()
    if not name:
        return None

    config.save_preset(
        name,
        Selection(apps=list(selection.apps), sites=list(selection.sites), duration_minutes=selection.duration_minutes),
    )
    console.print(f'[green]Пресет "{name}" сохранён.[/green] Быстрый запуск: `block {name}`.')

    if Confirm.ask(f'Установить команду "{name}" прямо в терминале (просто набирать `{name}`)?', default=False):
        if not installer.is_valid_alias_name(name):
            console.print("[yellow]Имя не годится для команды — только буквы, цифры и подчёркивание.[/yellow]")
        else:
            conflict = installer.existing_command_conflict(name)
            proceed = True
            if conflict:
                proceed = Confirm.ask(
                    f'Команда "{name}" уже существует ({conflict}). Всё равно переопределить?', default=False
                )
            if proceed:
                changed = installer.install_alias(name)
                if changed:
                    console.print(f'[green]Команда "{name}" установлена.[/green] Открой новый терминал или выполни `source ~/.bashrc`.')
                else:
                    console.print("[yellow]Не нашёл ~/.bashrc или ~/.zshrc — не получилось установить команду.[/yellow]")
            else:
                console.print("[dim]Пропущено.[/dim]")
    return name


def confirm_and_start(selection: Selection, preset_name: str | None) -> int:
    body = (
        f"[bold]Приложения:[/bold] {', '.join(a.name for a in selection.apps) or '—'}\n"
        f"[bold]Сайты:[/bold] {', '.join(selection.sites) or '—'}\n"
        f"[bold]Время:[/bold] {selection.duration_minutes} мин."
    )
    console.print()
    console.print(
        Panel(
            "[bold yellow]📵  Убери телефон подальше — в сумку или тумбочку — и включи его без звука.[/bold yellow]\n\n"
            + body,
            title="Перед стартом",
            border_style="yellow",
        )
    )
    if not Confirm.ask("Начинаем?", default=False):
        console.print("[dim]Отменено.[/dim]")
        return 1

    console.print("\n[dim]Дальше понадобится пароль sudo — демон блокировки должен уметь работать в фоне даже после закрытия терминала.[/dim]")
    ok, message = runner.start_block(selection, preset_name)
    if ok:
        console.print(f"[bold green]{message}[/bold green]")
        console.print("[dim]Статус: `block --status`.  Досрочно снять: `block --stop`.[/dim]")
        return 0
    console.print(f"[bold red]{message}[/bold red]")
    return 1


def run_wizard(initial_preset: str | None = None) -> int:
    _header()
    state = config.load_state()
    if state.active:
        minutes_left = int(state.seconds_left // 60)
        console.print(f"[yellow]Блокировка уже активна[/yellow] — осталось {minutes_left} мин.")
        console.print("[dim]Досрочно снять: `block --stop`.[/dim]")
        return 0

    presets = config.load_presets()

    if initial_preset:
        sel = presets.get(initial_preset)
        if sel is None:
            console.print(f'[red]Пресета "{initial_preset}" нет.[/red] Доступные: {", ".join(presets) or "—"}')
            return 1
        return confirm_and_start(sel, initial_preset)

    if presets:
        console.print(f"\nСохранённые пресеты: [cyan]{', '.join(presets)}[/cyan]")
        name = Prompt.ask("Запустить один из них? Имя (пусто — настроить вручную)", default="").strip()
        if name:
            sel = presets.get(name)
            if sel is None:
                console.print(f'[yellow]Пресета "{name}" нет — настраиваю вручную.[/yellow]')
            else:
                return confirm_and_start(sel, name)

    apps = pick_apps()
    sites = pick_sites()
    if not apps and not sites:
        console.print("\n[red]Не выбрано ни одного приложения или сайта — блокировать нечего.[/red]")
        return 1
    duration = pick_duration()

    selection = Selection(apps=apps, sites=sites, duration_minutes=duration)
    config.save_selection(selection)
    preset_name = maybe_save_preset(selection)
    return confirm_and_start(selection, preset_name)
