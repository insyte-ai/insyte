"""Conversation application service — persists Studio chats in the metadata database.

Reuses the project's existing ``metadata.sqlite`` (no separate ``studio.sqlite``).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from insyte.metadata.models import Conversation, ConversationMessage, SavedInvestigation
from insyte.metadata.repository import MetadataRepository

if TYPE_CHECKING:
    from insyte.studio.context import ChatContext


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex[:12]}"


def new_analysis_id() -> str:
    return f"an_{uuid.uuid4().hex[:12]}"


def new_investigation_id() -> str:
    return f"inv_{uuid.uuid4().hex[:12]}"


class ConversationService:
    """Create, list, and append to Studio conversations."""

    def __init__(self, metadata: MetadataRepository, project: str) -> None:
        self._metadata = metadata
        self._project = project

    def create(self, title: str = "New analysis") -> Conversation:
        return self._metadata.create_conversation(new_conversation_id(), self._project, title)

    def list_all(self) -> list[Conversation]:
        return self._metadata.list_conversations(self._project)

    def get(self, conversation_id: str) -> Conversation | None:
        return self._metadata.get_conversation(conversation_id)

    def set_title(self, conversation_id: str, title: str) -> None:
        self._metadata.set_conversation_title(conversation_id, title)

    def autotitle_from_question(self, conversation_id: str, question: str) -> None:
        """Name an untitled conversation after its first question."""

        conversation = self._metadata.get_conversation(conversation_id)
        if conversation is None or conversation.title not in ("", "New analysis"):
            return
        title = " ".join(question.split())
        if len(title) > 48:
            title = title[:47].rstrip() + "…"
        self._metadata.set_conversation_title(conversation_id, title[:1].upper() + title[1:])

    def delete(self, conversation_id: str) -> bool:
        return self._metadata.delete_conversation(conversation_id)

    def add_message(
        self, conversation_id: str, role: str, content: str, analysis_id: str | None = None
    ) -> ConversationMessage:
        return self._metadata.add_message(conversation_id, role, content, analysis_id)

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        return self._metadata.list_messages(conversation_id)

    def save_context(
        self, conversation_id: str, context: ChatContext, analysis_id: str | None = None
    ) -> None:
        self._metadata.save_context_snapshot(conversation_id, analysis_id, context.to_dict())

    def latest_context(self, conversation_id: str) -> ChatContext | None:
        from insyte.studio.context import ChatContext

        snapshot = self._metadata.latest_context_snapshot(conversation_id)
        if snapshot is None:
            return None
        return ChatContext.from_dict(snapshot.context_json)

    def save_analysis(
        self,
        analysis_id: str,
        question: str,
        summary: str | None,
        structured_result_json: str | None,
        conversation_id: str | None = None,
    ) -> None:
        self._metadata.save_analysis_result(
            analysis_id, question, summary, structured_result_json, conversation_id
        )

    def get_analysis(self, analysis_id: str) -> str | None:
        return self._metadata.get_analysis_result(analysis_id)

    def get_analysis_request(self, analysis_id: str) -> tuple[str, str | None] | None:
        return self._metadata.get_analysis_request(analysis_id)

    def save_investigation(
        self,
        *,
        analysis_id: str,
        question: str,
        summary: str,
        result_json: dict,
        conversation_id: str | None = None,
        title: str | None = None,
    ) -> SavedInvestigation:
        return self._metadata.save_investigation(
            new_investigation_id(),
            self._project,
            analysis_id,
            title or _investigation_title(question, summary),
            summary,
            question,
            result_json,
            conversation_id,
        )

    def investigations(self) -> list[SavedInvestigation]:
        return self._metadata.list_investigations(self._project)

    def investigation(self, investigation_id: str) -> SavedInvestigation | None:
        inv = self._metadata.get_investigation(investigation_id)
        if inv is None or inv.project != self._project:
            return None
        return inv

    def set_investigation_title(self, investigation_id: str, title: str) -> bool:
        inv = self.investigation(investigation_id)
        if inv is None:
            return False
        return self._metadata.set_investigation_title(investigation_id, title)

    def delete_investigation(self, investigation_id: str) -> bool:
        inv = self.investigation(investigation_id)
        if inv is None:
            return False
        return self._metadata.delete_investigation(investigation_id)


def _investigation_title(question: str, summary: str) -> str:
    title = " ".join((question or summary or "Investigation").split())
    if len(title) > 64:
        title = title[:63].rstrip() + "…"
    return title[:1].upper() + title[1:]
