"""Conversation endpoints — create chats and enqueue analysis jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from insyte.metadata.models import Conversation
from insyte.services.conversation_service import new_analysis_id
from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_pending, get_services
from insyte.studio.schemas import ConversationCreate, MessageRequest

router = APIRouter()


def _conversation(conversation: Conversation) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


@router.post("/conversations")
def create_conversation(
    body: ConversationCreate, services: ProjectServices = Depends(get_services)
) -> dict:
    return _conversation(services.conversations.create(body.title))


@router.get("/conversations")
def list_conversations(services: ProjectServices = Depends(get_services)) -> dict:
    return {"conversations": [_conversation(c) for c in services.conversations.list_all()]}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, services: ProjectServices = Depends(get_services)
) -> dict:
    conversation = services.conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = services.conversations.messages(conversation_id)
    return {
        "conversation": _conversation(conversation),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "analysis_id": m.analysis_id,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str, services: ProjectServices = Depends(get_services)
) -> dict:
    if not services.conversations.delete(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True}


@router.post("/conversations/{conversation_id}/messages")
def post_message(
    conversation_id: str,
    body: MessageRequest,
    services: ProjectServices = Depends(get_services),
    pending: dict = Depends(get_pending),
) -> dict:
    if services.conversations.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    analysis_id = new_analysis_id()
    # Name the conversation after its first question (before adding the message).
    services.conversations.autotitle_from_question(conversation_id, body.content)
    services.conversations.add_message(conversation_id, "user", body.content)
    pending[analysis_id] = {
        "question": body.content,
        "conversation_id": conversation_id,
        "detailed": body.detailed,
    }
    return {
        "analysis_id": analysis_id,
        "stream_url": f"/api/analyses/{analysis_id}/events",
    }
