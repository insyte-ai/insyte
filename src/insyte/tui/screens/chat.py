"""The main chat screen: sidebar, conversation log, prompt, and status bar."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Static

from insyte.tui.controller import Response, ResponseKind
from insyte.tui.widgets.prompt_input import PromptInput
from insyte.tui.widgets.result_card import ResultCard
from insyte.tui.widgets.sidebar import NAV_COMMANDS, Sidebar
from insyte.tui.widgets.status_bar import StatusBar
from insyte.tui.widgets.table_panel import TablePanel

_WELCOME = (
    "[b]Welcome to Insyte.[/b] Ask about your database in plain language, "
    "or type [b]/help[/b].\n"
    "[dim]Examples: 'weekly completed revenue', 'completed revenue by city', "
    "'payment failure rate'.[/dim]"
)


class ChatScreen(Screen):
    """Interactive analytics chat."""

    BINDINGS = [
        Binding("ctrl+l", "focus_prompt", "Prompt"),
        Binding("ctrl+k", "clear_chat", "Clear"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            yield Sidebar(id="sidebar")
            with Vertical(id="main"):
                yield VerticalScroll(id="chat-log")
                yield PromptInput()
        yield StatusBar(self.app.status_text)  # type: ignore[attr-defined]
        yield Footer()

    def on_mount(self) -> None:
        self._log(Static(_WELCOME, classes="msg"))
        self.query_one(PromptInput).focus()

    # -- events ------------------------------------------------------------------------------

    def on_input_submitted(self, event: PromptInput.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if text:
            self._submit(text)

    def on_button_pressed(self, event: object) -> None:
        button_id = getattr(getattr(event, "button", None), "id", None)
        if button_id == "nav-new":
            self.query_one(PromptInput).focus()
        elif button_id in NAV_COMMANDS:
            self._submit(NAV_COMMANDS[button_id])

    def action_focus_prompt(self) -> None:
        self.query_one(PromptInput).focus()

    def action_clear_chat(self) -> None:
        self.query_one("#chat-log").remove_children()

    # -- processing --------------------------------------------------------------------------

    def _submit(self, text: str) -> None:
        self._log(Static(f"[b]❯[/b] {text}", classes="user-msg"))
        thinking = Static("⋯ analyzing…", classes="thinking")
        self._log(thinking)
        self._process(text, thinking)

    @work(thread=True)
    def _process(self, text: str, thinking: Static) -> None:
        try:
            response = self.app.controller.run(text)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - surface any failure as a chat message
            response = Response.message(f"Something went wrong: {exc}", "error")
        self.app.call_from_thread(self._render_response, response, thinking)

    def _render_response(self, response: Response, thinking: Static) -> None:
        thinking.remove()
        if response.kind is ResponseKind.clear:
            self.query_one("#chat-log").remove_children()
            return
        if response.kind is ResponseKind.quit:
            self.app.exit()
            return
        if response.kind is ResponseKind.message:
            self._log(Static(response.text, classes=f"msg {response.level}"))
        elif response.kind is ResponseKind.analysis and response.analysis is not None:
            self._log(ResultCard(response.analysis))
        elif response.kind is ResponseKind.comparison and response.comparison is not None:
            self._render_comparison(response)
        elif response.kind is ResponseKind.table:
            self._log(TablePanel(response.columns, response.rows, title=response.title))

    def _render_comparison(self, response: Response) -> None:
        comparison = response.comparison
        assert comparison is not None
        self._log(Static(f"[b]{comparison.label}[/b]\n{comparison.summary}", classes="msg"))
        rows = [
            [comparison.current.label, _fmt(comparison.current_value)],
            [comparison.baseline.label, _fmt(comparison.baseline_value)],
        ]
        if comparison.absolute_change is not None:
            rows.append(["Change", f"{comparison.absolute_change:+.2f}"])
        self._log(TablePanel(["Period", "Value"], rows))

    def _log(self, widget: Widget) -> None:
        log = self.query_one("#chat-log")
        log.mount(widget)
        log.scroll_end(animate=False)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"
