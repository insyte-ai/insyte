"""The Insyte interactive terminal UI (Textual).

Keep these exports lazy: shared natural-language code imports ``insyte.tui.intent``, and eager
package imports would otherwise pull the entire terminal UI into the standalone Studio app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from insyte.tui.app import InsyteApp
    from insyte.tui.controller import ChatController

__all__ = ["ChatController", "InsyteApp"]


def __getattr__(name: str) -> Any:
    if name == "InsyteApp":
        from insyte.tui.app import InsyteApp

        return InsyteApp
    if name == "ChatController":
        from insyte.tui.controller import ChatController

        return ChatController
    raise AttributeError(name)
