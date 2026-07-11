"""The Insyte Textual application."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen

from insyte.tui.controller import ChatController
from insyte.tui.screens.chat import ChatScreen


class InsyteApp(App):
    """Interactive terminal analytics UI (``insyte chat``)."""

    CSS_PATH = "styles/app.tcss"
    TITLE = "Insyte"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, controller: ChatController, project_name: str, status_text: str) -> None:
        super().__init__()
        self.controller = controller
        self.status_text = status_text
        self.sub_title = project_name

    def get_default_screen(self) -> Screen:
        return ChatScreen()
