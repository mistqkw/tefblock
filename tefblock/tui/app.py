from __future__ import annotations

import os

# Терминалы с прозрачностью/blur (частый случай в Hyprland-конфигах с kitty)
# иногда «размазывают» экран при частых полных перерисовках TUI. Снижаем
# частоту кадров и отключаем анимации/сглаженный скролл, чтобы не провоцировать
# это на уровне композитора. Учитывается только если пользователь сам не задал
# эти переменные окружения.
os.environ.setdefault("TEXTUAL_ANIMATIONS", "none")
os.environ.setdefault("TEXTUAL_FPS", "20")
os.environ.setdefault("TEXTUAL_SMOOTH_SCROLL", "0")

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from .. import config
from .screens import ActiveBlockScreen, MainScreen, WarningScreen

CSS_PATH = Path(__file__).parent / "tui.css"


class TeFBlockApp(App):
    TITLE = "TeFBlock"
    CSS_PATH = str(CSS_PATH)
    BINDINGS = [Binding("ctrl+q", "quit", "Выход")]

    def __init__(self, initial_preset: str | None = None) -> None:
        super().__init__()
        self.initial_preset = initial_preset
        self.main_screen_available = False

    def on_mount(self) -> None:
        state = config.load_state()
        if state.active:
            self.push_screen(ActiveBlockScreen())
            return
        if self.initial_preset:
            presets = config.load_presets()
            sel = presets.get(self.initial_preset)
            if sel is not None:
                self.push_screen(WarningScreen(sel, preset_name=self.initial_preset))
                return
        self.main_screen_available = True
        self.push_screen(MainScreen())


def run_app(initial_preset: str | None = None) -> int:
    app = TeFBlockApp(initial_preset=initial_preset)
    app.run()
    return 0
