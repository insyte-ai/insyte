"""The prompt input at the bottom of the chat."""

from __future__ import annotations

from textual.widgets import Input


class PromptInput(Input):
    """A single-line prompt for asking the database questions."""

    def __init__(self) -> None:
        super().__init__(
            placeholder="Ask your database…  (try 'weekly completed revenue' or /help)",
            id="prompt",
        )
