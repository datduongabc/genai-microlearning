import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_course_owner
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import ChatMessage, ChatRole, ChatSession, Course, User
from app.retrieval.search import retrieve
from app.services.chat import HISTORY_TURNS, answer_question, condense_query

router = APIRouter(prefix="/courses/{course_id}/chat", tags=["chat"], dependencies=[Depends(get_current_user)])


# Every message is at least two model calls (condensing the question, answering it),
# and a long one is more expensive still.
MAX_CHAT_MESSAGE_CHARS = 4000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS)
    session_id: uuid.UUID | None = None


class CitationResponse(BaseModel):
    chunk_id: str
    source_ref: str = ""


class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[CitationResponse]


class ChatResponse(BaseModel):
    session_id: uuid.UUID
    message: ChatMessageResponse


def _get_or_create_session(db: Session, course_id: uuid.UUID, session_id: uuid.UUID | None, title_seed: str) -> ChatSession:
    if session_id is not None:
        session = db.get(ChatSession, session_id)
        if session is None or session.course_id != course_id:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return session
    title = title_seed[:40] + ("..." if len(title_seed) > 40 else "")
    session = ChatSession(course_id=course_id, title=title)
    db.add(session)
    db.flush()
    return session


@router.post("", response_model=ChatResponse)
@limiter.limit("20/minute", key_func=user_or_address)  # two model calls or more per message
def post_chat_message(
    request: Request, course_id: uuid.UUID, payload: ChatRequest, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_course_owner(db.get(Course, course_id), current_user)

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    session = _get_or_create_session(db, course_id, payload.session_id, message)

    user_message = ChatMessage(session_id=session.id, role=ChatRole.user, content=message, citations=[])
    db.add(user_message)
    db.flush()

    history_rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id, ChatMessage.id != user_message.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_TURNS)
        .all()
    )
    history = [{"role": r.role.value, "content": r.content} for r in reversed(history_rows)]

    retrieval_query = condense_query(message, history)
    context_chunks = retrieve(db, course_id, retrieval_query, top_k=5)
    answer = answer_question(message, context_chunks, history)

    assistant_message = ChatMessage(
        session_id=session.id,
        role=ChatRole.assistant,
        content=answer.answer,
        citations=[c.model_dump() for c in answer.citations],
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    return ChatResponse(
        session_id=session.id,
        message=ChatMessageResponse(
            id=assistant_message.id,
            role=assistant_message.role.value,
            content=assistant_message.content,
            citations=assistant_message.citations,
        ),
    )


@router.get("/{session_id}/history", response_model=list[ChatMessageResponse])
def get_chat_history(
    course_id: uuid.UUID, session_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_course_owner(db.get(Course, course_id), current_user)

    session = db.get(ChatSession, session_id)
    if session is None or session.course_id != course_id:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return [
        ChatMessageResponse(id=m.id, role=m.role.value, content=m.content, citations=m.citations)
        for m in session.messages
    ]
