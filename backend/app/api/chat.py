import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import count_learner_chat, get_current_user, require_course_reader
from app.auth.rate_limit import limiter, user_or_address
from app.chat.service import (
    HISTORY_TURNS,
    ChatAnswer,
    condense_query_streamed,
    stream_answer,
)
from app.config import settings
from app.db import RequestSessionLocal, get_db
from app.models import ChatMessage, ChatRole, ChatSession, Course, User
from app.retrieval.search import retrieve_for_chat

router = APIRouter(prefix="/courses/{course_id}/chat", tags=["chat"], dependencies=[Depends(get_current_user)])


# Every message is at least two model calls (condensing the question, answering it),
# and a long one is more expensive still.
MAX_CHAT_MESSAGE_CHARS = 4000
chat_limit = limiter.shared_limit("20/minute", scope="chat", key_func=user_or_address)
# A silent connection can be closed by a proxy on the way, and the answer may take this long to start.
HEARTBEAT_SECONDS = 15
_retrieval_slots = asyncio.Semaphore(settings.chat_retrieval_concurrency)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS)
    session_id: uuid.UUID | None = None


class CitationResponse(BaseModel):
    chunk_id: str
    source_ref: str = ""
    content: str = ""


class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[CitationResponse]


def _get_or_create_session(db: Session, course_id: uuid.UUID, session_id: uuid.UUID | None, title_seed: str,
    user_id: uuid.UUID, module_id: uuid.UUID | None = None,
) -> ChatSession:
    if session_id is not None:
        session = db.get(ChatSession, session_id)
        # Row level security hides another person's session already; the user check says so here too.
        if (session is None or session.course_id != course_id or session.module_id != module_id
                or session.user_id != user_id):
            raise HTTPException(status_code=404, detail="Chat session not found")
        return session
    title = title_seed[:40] + ("..." if len(title_seed) > 40 else "")
    session = ChatSession(course_id=course_id, user_id=user_id, module_id=module_id, title=title)
    db.add(session)
    db.flush()
    return session


@dataclass(frozen=True)
class _Turn:
    user_id: uuid.UUID
    course_id: uuid.UUID
    session_id: uuid.UUID
    message: str
    history: list[dict]


def _start_turn(db: Session, course_id: uuid.UUID, payload: ChatRequest, user: User) -> _Turn:
    """Saves the question and closes the session: an answer takes seconds, and must not hold a connection."""
    require_course_reader(db, db.get(Course, course_id), user)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    count_learner_chat(db, user)
    session = _get_or_create_session(db, course_id, payload.session_id, message, user.id)
    question = ChatMessage(session_id=session.id, role=ChatRole.user, content=message, citations=[])
    db.add(question)
    db.flush()
    rows = (db.query(ChatMessage).filter(ChatMessage.session_id == session.id, ChatMessage.id != question.id)
            .order_by(ChatMessage.created_at.desc()).limit(HISTORY_TURNS).all())
    history = [{"role": r.role.value, "content": r.content} for r in reversed(rows)]
    turn = _Turn(user.id, course_id, session.id, message, history)
    db.commit()
    db.close()
    return turn


def _user_session(user_id: uuid.UUID) -> Session:
    db = RequestSessionLocal()
    db.info["rls_user_id"] = user_id
    return db


def _search(turn: _Turn, query: str) -> list:
    return retrieve_for_chat(_user_session(turn.user_id), turn.course_id, query, top_k=8)


def _save_answer(turn: _Turn, answer: ChatAnswer) -> ChatMessageResponse:
    with _user_session(turn.user_id) as db:
        saved = ChatMessage(session_id=turn.session_id, role=ChatRole.assistant, content=answer.answer,
                            citations=[c.model_dump() for c in answer.citations])
        db.add(saved)
        db.commit()
        return ChatMessageResponse(id=saved.id, role=saved.role.value, content=saved.content, citations=saved.citations)


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _answer_events(turn: _Turn) -> AsyncIterator[str]:
    yield _event("session", {"session_id": str(turn.session_id)})
    try:
        query = await condense_query_streamed(turn.message, turn.history)
        async with _retrieval_slots:
            chunks = await asyncio.to_thread(_search, turn, query)
        async for part in stream_answer(turn.message, chunks, turn.history, standalone=query):
            if isinstance(part, str):
                yield _event("delta", {"text": part})
            else:
                saved = await asyncio.to_thread(_save_answer, turn, part)
                yield _event("done", saved.model_dump(mode="json"))
    except Exception:
        # The 200 has already gone out, so this is the only way left to tell the reader.
        logger.exception("chat stream failed in session %s", turn.session_id)
        yield _event("error", {"detail": "Không tạo được câu trả lời, vui lòng thử lại."})


async def _with_heartbeat(events: AsyncIterator[str], seconds: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    iterator = aiter(events)
    pending = None
    try:
        while True:
            pending = pending or asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=seconds)
            if not done:
                yield ": ping\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                pending = None
                return
            pending = None
            yield event
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


@router.post("/stream")
@chat_limit
async def stream_chat_message(
    request: Request, course_id: uuid.UUID, payload: ChatRequest, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The answer as server-sent events: `session`, then `delta` for each piece, then `done` with the saved message,
    or `error` when it fails after the response has begun."""
    turn = await run_in_threadpool(_start_turn, db, course_id, payload, current_user)
    return StreamingResponse(_with_heartbeat(_answer_events(turn)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class LatestChatResponse(BaseModel):
    session_id: uuid.UUID | None
    messages: list[ChatMessageResponse]


def _latest_session(db: Session, course_id: uuid.UUID, user_id: uuid.UUID) -> ChatSession | None:
    # By its newest message, or when it was opened if it has none yet: "Cuộc trò chuyện mới" opens
    # an empty one, and a reload must bring that back rather than the conversation it replaced.
    used = func.coalesce(func.max(ChatMessage.created_at), ChatSession.created_at)
    return db.scalar(
        select(ChatSession).outerjoin(ChatMessage)
        .where(ChatSession.course_id == course_id, ChatSession.user_id == user_id,
               ChatSession.module_id.is_(None))
        .group_by(ChatSession.id).order_by(used.desc()).limit(1)
    )


@router.post("/sessions", status_code=201)
@limiter.limit("30/minute", key_func=user_or_address)
def start_new_chat(
    request: Request, course_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A new, empty course chat, which /latest returns until another is used. Clicking twice opens
    one, not two: an empty latest session is given back as it is."""
    require_course_reader(db, db.get(Course, course_id), current_user)
    latest = _latest_session(db, course_id, current_user.id)
    if latest is not None and not latest.messages:
        return {"session_id": latest.id}
    session = ChatSession(course_id=course_id, user_id=current_user.id, module_id=None, title=None)
    db.add(session)
    db.commit()
    return {"session_id": session.id}


@router.get("/latest", response_model=LatestChatResponse)
def get_latest_chat(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """The course chat this person used last, so that reloading the page or opening it on another
    machine continues it. Last used, not last created: by its newest message."""
    require_course_reader(db, db.get(Course, course_id), current_user)
    session = _latest_session(db, course_id, current_user.id)
    if session is None:
        return LatestChatResponse(session_id=None, messages=[])
    return LatestChatResponse(session_id=session.id, messages=[
        ChatMessageResponse(id=m.id, role=m.role.value, content=m.content, citations=m.citations)
        for m in session.messages
    ])


@router.get("/{session_id}/history", response_model=list[ChatMessageResponse])
def get_chat_history(
    course_id: uuid.UUID, session_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_course_reader(db, db.get(Course, course_id), current_user)

    session = db.get(ChatSession, session_id)
    if (session is None or session.course_id != course_id or session.module_id is not None
            or session.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Chat session not found")
    return [
        ChatMessageResponse(id=m.id, role=m.role.value, content=m.content, citations=m.citations)
        for m in session.messages
    ]
