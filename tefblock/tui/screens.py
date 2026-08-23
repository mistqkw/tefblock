from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets import SelectionList
from textual.widgets.selection_list import Selection

from .. import appscan, config, installer, runner
from ..config import AppEntry, Selection as SelectionData
from ..domains import normalize_site_input

DURATION_PRESETS = [("1 минута", 1), ("20 минут", 20), ("1 час", 60)]


class MainScreen(Screen):
    """Основной экран: выбор приложений, сайтов, таймера и пресетов."""

    def __init__(self) -> None:
        super().__init__()
        self.selection = config.load_selection()
        self.all_apps: list[AppEntry] = appscan.scan_installed_apps()
        self.apps_by_id: dict[str, AppEntry] = {a.app_id: a for a in self.all_apps}
        self.selected_app_ids: set[str] = {a.app_id for a in self.selection.apps if a.app_id in self.apps_by_id}
        self._pending_alias_confirm: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent(initial="tab-apps"):
            with TabPane("Приложения", id="tab-apps"):
                yield Input(placeholder="Поиск приложения…", id="app-search")
                yield SelectionList(id="app-list")
            with TabPane("Сайты", id="tab-sites"):
                with Horizontal(classes="row"):
                    yield Input(placeholder="youtube.com или ссылка на видео…", id="site-input")
                    yield Button("Добавить", id="site-add", variant="primary")
                yield VerticalScroll(Vertical(id="site-rows"), classes="rows-scroll")
            with TabPane("Таймер", id="tab-timer"):
                yield Label("На сколько заблокировать?", classes="section-title")
                with Horizontal(classes="row"):
                    for label, minutes in DURATION_PRESETS:
                        yield Button(label, id=f"timer-{minutes}", classes="timer-btn")
                with Horizontal(classes="row"):
                    yield Input(placeholder="своё число минут", id="timer-custom")
                yield Static("", id="timer-current", classes="section-title")
            with TabPane("Пресеты", id="tab-presets"):
                yield VerticalScroll(Vertical(id="preset-rows"), classes="rows-scroll")
                with Horizontal(classes="row"):
                    yield Input(placeholder="имя пресета, напр. work", id="preset-name")
                    yield Button("Сохранить текущий выбор", id="preset-save", variant="primary")
        yield Static("", id="summary")
        yield Button("▶  Начать блокировку", id="start-btn", variant="success")
        yield Footer()

    async def on_mount(self) -> None:
        self.refresh_app_list()
        await self.refresh_site_rows()
        await self.refresh_preset_rows()
        self.refresh_timer_label()
        self.refresh_summary()

    # ---------- приложения ----------

    def refresh_app_list(self, filter_text: str = "") -> None:
        selection_list = self.query_one("#app-list", SelectionList)
        selection_list.clear_options()
        needle = filter_text.strip().lower()
        for app in self.all_apps:
            if needle and needle not in app.name.lower():
                continue
            selection_list.add_option(Selection(app.name, app.app_id, app.app_id in self.selected_app_ids))

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        if event.selection_list.id != "app-list":
            return
        visible_ids = {opt.value for opt in event.selection_list.options}
        self.selected_app_ids -= visible_ids
        self.selected_app_ids |= set(event.selection_list.selected)
        self.selection.apps = [self.apps_by_id[i] for i in self.selected_app_ids if i in self.apps_by_id]
        config.save_selection(self.selection)
        self.refresh_summary()

    # ---------- сайты ----------

    async def add_site(self) -> None:
        field = self.query_one("#site-input", Input)
        raw = field.value.strip()
        if not raw:
            return
        canonical, domains = normalize_site_input(raw)
        if not canonical or not domains:
            self.notify("Не удалось распознать сайт или ссылку", severity="error")
            return
        if canonical not in self.selection.sites:
            self.selection.sites.append(canonical)
            config.save_selection(self.selection)
        field.value = ""
        await self.refresh_site_rows()
        self.refresh_summary()

    async def remove_site(self, canonical: str) -> None:
        if canonical in self.selection.sites:
            self.selection.sites.remove(canonical)
            config.save_selection(self.selection)
        await self.refresh_site_rows()
        self.refresh_summary()

    async def refresh_site_rows(self) -> None:
        container = self.query_one("#site-rows", Vertical)
        await container.remove_children()
        if not self.selection.sites:
            await container.mount(Static("Пока ничего не добавлено.", classes="empty-hint"))
            return
        for canonical in self.selection.sites:
            remove_btn = Button("✕", classes="remove-btn")
            remove_btn.site_key = canonical  # type: ignore[attr-defined]
            row = Horizontal(Label(canonical, classes="row-label"), remove_btn, classes="row")
            await container.mount(row)

    # ---------- таймер ----------

    def set_block_duration(self, minutes: int) -> None:
        self.selection.duration_minutes = max(1, minutes)
        config.save_selection(self.selection)
        self.refresh_timer_label()
        self.refresh_summary()

    def refresh_timer_label(self) -> None:
        self.query_one("#timer-current", Static).update(
            f"Выбрано: {self.selection.duration_minutes} мин."
        )

    # ---------- пресеты ----------

    async def save_current_as_preset(self) -> None:
        name_field = self.query_one("#preset-name", Input)
        name = name_field.value.strip()
        if not name:
            self.notify("Введи имя пресета", severity="error")
            return
        if not self.selection.apps and not self.selection.sites:
            self.notify("Сначала выбери хотя бы приложение или сайт", severity="error")
            return
        config.save_preset(name, SelectionData(
            apps=list(self.selection.apps),
            sites=list(self.selection.sites),
            duration_minutes=self.selection.duration_minutes,
        ))
        name_field.value = ""
        await self.refresh_preset_rows()
        self.notify(f'Пресет "{name}" сохранён')

    async def refresh_preset_rows(self) -> None:
        container = self.query_one("#preset-rows", Vertical)
        await container.remove_children()
        presets = config.load_presets()
        aliases = installer.list_installed_aliases()
        if not presets:
            await container.mount(Static("Пока нет ни одного пресета.", classes="empty-hint"))
            return
        for name, sel in presets.items():
            summary = f"{name} — {len(sel.apps)} прил., {len(sel.sites)} сайтов, {sel.duration_minutes} мин"
            if name in aliases:
                summary += "  (команда установлена)"
            load_btn = Button("Загрузить", classes="preset-btn")
            load_btn.preset_action, load_btn.preset_name = "load", name  # type: ignore[attr-defined]
            alias_btn = Button("Команда", classes="preset-btn")
            alias_btn.preset_action, alias_btn.preset_name = "alias", name  # type: ignore[attr-defined]
            del_btn = Button("Удалить", classes="preset-btn danger")
            del_btn.preset_action, del_btn.preset_name = "delete", name  # type: ignore[attr-defined]
            row = Horizontal(
                Label(summary, classes="row-label"), load_btn, alias_btn, del_btn, classes="row"
            )
            await container.mount(row)

    async def load_preset(self, name: str) -> None:
        presets = config.load_presets()
        sel = presets.get(name)
        if sel is None:
            return
        self.selection = SelectionData(apps=list(sel.apps), sites=list(sel.sites), duration_minutes=sel.duration_minutes)
        self.selected_app_ids = {a.app_id for a in self.selection.apps}
        config.save_selection(self.selection)
        search = self.query_one("#app-search", Input)
        search.value = ""
        self.refresh_app_list()
        await self.refresh_site_rows()
        self.refresh_timer_label()
        self.refresh_summary()
        self.notify(f'Пресет "{name}" загружен')

    async def delete_preset(self, name: str) -> None:
        config.delete_preset(name)
        installer.uninstall_alias(name)
        await self.refresh_preset_rows()
        self.notify(f'Пресет "{name}" удалён')

    async def toggle_alias(self, name: str) -> None:
        if name in self._pending_alias_confirm:
            self._pending_alias_confirm.discard(name)
            changed = installer.install_alias(name)
            if changed:
                self.notify(f'Команда "{name}" установлена. Выполни `source ~/.bashrc` (или новый терминал).')
                await self.refresh_preset_rows()
            else:
                self.notify("Не найден ~/.bashrc или ~/.zshrc — не удалось установить команду.", severity="error")
            return
        if not installer.is_valid_alias_name(name):
            self.notify("Имя пресета должно быть похоже на команду: буквы/цифры/подчёркивание.", severity="error")
            return
        conflict = installer.existing_command_conflict(name)
        if conflict:
            self._pending_alias_confirm.add(name)
            self.notify(
                f'Команда "{name}" уже существует ({conflict}). Нажми «Команда» ещё раз, чтобы всё равно переопределить.',
                severity="warning",
                timeout=7,
            )
            return
        changed = installer.install_alias(name)
        if changed:
            self.notify(f'Команда "{name}" установлена. Выполни `source ~/.bashrc` (или новый терминал).')
            await self.refresh_preset_rows()
        else:
            self.notify("Не найден ~/.bashrc или ~/.zshrc — не удалось установить команду.", severity="error")

    # ---------- общее ----------

    def refresh_summary(self) -> None:
        text = (
            f"Выбрано: {len(self.selection.apps)} прил., {len(self.selection.sites)} сайтов, "
            f"{self.selection.duration_minutes} мин."
        )
        self.query_one("#summary", Static).update(text)

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "app-search":
            self.refresh_app_list(event.value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "site-input":
            await self.add_site()
        elif event.input.id == "timer-custom":
            self._apply_custom_timer(event.value)
        elif event.input.id == "preset-name":
            await self.save_current_as_preset()

    def _apply_custom_timer(self, raw: str) -> None:
        raw = raw.strip()
        if not raw.isdigit() or int(raw) <= 0:
            self.notify("Введи целое число минут больше нуля", severity="error")
            return
        self.set_block_duration(int(raw))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button
        bid = btn.id
        if bid == "start-btn":
            await self.on_start_pressed()
        elif bid == "site-add":
            await self.add_site()
        elif bid == "preset-save":
            await self.save_current_as_preset()
        elif bid and bid.startswith("timer-"):
            self.set_block_duration(int(bid.removeprefix("timer-")))
        elif hasattr(btn, "site_key"):
            await self.remove_site(btn.site_key)  # type: ignore[attr-defined]
        elif hasattr(btn, "preset_action"):
            action, name = btn.preset_action, btn.preset_name  # type: ignore[attr-defined]
            if action == "load":
                await self.load_preset(name)
            elif action == "delete":
                await self.delete_preset(name)
            elif action == "alias":
                await self.toggle_alias(name)

    async def on_start_pressed(self) -> None:
        if not self.selection.apps and not self.selection.sites:
            self.notify("Выбери хотя бы одно приложение или сайт для блокировки", severity="error")
            return
        await self.app.push_screen(WarningScreen(self.selection))


class WarningScreen(Screen):
    """Предупреждение перед началом блокировки."""

    def __init__(self, selection: SelectionData, preset_name: str | None = None) -> None:
        super().__init__()
        self.selection = selection
        self.preset_name = preset_name

    def compose(self) -> ComposeResult:
        with Vertical(id="warning-box"):
            yield Static("📵", classes="warning-emoji")
            yield Static("Убери телефон подальше — в сумку или тумбочку — и включи его без звука.", classes="warning-text")
            yield Static(
                "Когда блокировка начнётся, снять её раньше времени будет непросто.\n"
                f"Приложения: {', '.join(a.name for a in self.selection.apps) or '—'}\n"
                f"Сайты: {', '.join(self.selection.sites) or '—'}\n"
                f"Длительность: {self.selection.duration_minutes} мин.",
                classes="warning-details",
            )
            with Horizontal(classes="row"):
                yield Button("Я понял(а), начать", id="confirm-btn", variant="error")
                yield Button("Отмена", id="cancel-btn")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            if getattr(self.app, "main_screen_available", False):
                self.app.pop_screen()
            else:
                self.app.exit()
            return
        if event.button.id == "confirm-btn":
            await self.start_block()

    @work(exclusive=True)
    async def start_block(self) -> None:
        button = self.query_one("#confirm-btn", Button)
        button.disabled = True
        with self.app.suspend():
            print("\nTeFBlock: нужен пароль sudo, чтобы демон блокировки продолжал работать даже после закрытия терминала.\n")
            ok, message = runner.start_block(self.selection, self.preset_name)
        if ok:
            self.notify(message)
            self.app.pop_screen()
            await self.app.push_screen(ActiveBlockScreen())
        else:
            button.disabled = False
            self.notify(message, severity="error", timeout=8)


_STOP_BUTTON_LABEL = "⏹  Завершить раньше времени"
_STOP_BUTTON_CONFIRM_LABEL = "Точно? Нажми ещё раз — попросит пароль"


class ActiveBlockScreen(Screen):
    """Экран во время активной блокировки — обратный отсчёт."""

    def __init__(self) -> None:
        super().__init__()
        self._stop_confirm_pending = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="active-box"):
            yield Static("🔒 Блокировка активна", classes="active-title")
            yield Static("", id="countdown", classes="countdown")
            yield Static("", id="block-details", classes="warning-details")
            yield Static(
                "Работай. Это окно можно закрыть — блокировка останется в фоне.",
                classes="hint",
            )
            yield Button(_STOP_BUTTON_LABEL, id="stop-btn", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.update_view()
        self.set_interval(1.0, self.update_view)

    def update_view(self) -> None:
        state = config.load_state()
        if not state.active:
            self.notify("Блокировка завершена — можно возвращаться к делам.", timeout=10)
            self.finish()
            return
        total = int(state.seconds_left)
        hrs, rem = divmod(total, 3600)
        mins, secs = divmod(rem, 60)
        self.query_one("#countdown", Static).update(f"{hrs:02}:{mins:02}:{secs:02}")
        details = (
            f"Приложения: {', '.join(a.name for a in state.apps) or '—'}\n"
            f"Сайты: {', '.join(state.sites) or '—'}"
        )
        self.query_one("#block-details", Static).update(details)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "stop-btn":
            return
        if not self._stop_confirm_pending:
            self._stop_confirm_pending = True
            event.button.label = _STOP_BUTTON_CONFIRM_LABEL
            self.set_timer(4.0, self._reset_stop_confirm)
            return
        self._stop_confirm_pending = False
        await self._do_stop()

    def _reset_stop_confirm(self) -> None:
        self._stop_confirm_pending = False
        try:
            self.query_one("#stop-btn", Button).label = _STOP_BUTTON_LABEL
        except Exception:
            pass

    async def _do_stop(self) -> None:
        button = self.query_one("#stop-btn", Button)
        button.disabled = True
        button.label = "Останавливаю…"
        with self.app.suspend():
            print("\nTeFBlock: нужен пароль sudo, чтобы снять блокировку раньше времени.\n")
            ok, message = runner.request_stop()
        if ok:
            self.notify(message)
            self.finish()
        else:
            button.disabled = False
            button.label = _STOP_BUTTON_LABEL
            self.notify(message, severity="error", timeout=8)

    def finish(self) -> None:
        if getattr(self.app, "main_screen_available", False):
            self.app.pop_screen()
        else:
            self.app.exit()
