"""Sidebar navigation. Buttons post their command to the app for handling."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Static

# Button id -> chat command to run when pressed.
NAV_COMMANDS = {
    "nav-new": "/help",
    "nav-schema": "/schema",
    "nav-metrics": "/metrics",
    "nav-history": "/history",
}


class Sidebar(Vertical):
    """Left-hand navigation panel."""

    def compose(self) -> ComposeResult:
        yield Static("[b]Insyte[/b]", classes="brand")
        yield Static("Explore", classes="nav-heading")
        yield Button("New analysis", id="nav-new", variant="primary")
        yield Button("Schema", id="nav-schema")
        yield Button("Metrics", id="nav-metrics")
        yield Button("History", id="nav-history")
