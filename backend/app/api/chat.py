import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import timing
from app.api.deps import (
    count_learner_chat,
    get_current_user,
    require_authored_course,
    require_course_reader,
    return_learner_chat,
)
from app.auth.rate_limit import limiter, user_or_address
from app.chat import cache as chat_cache
from app.chat.intent import small_talk
from app.chat.service import (
    HISTORY_TURNS,
    INSTRUCTIONS_WITHHELD,
    INSTRUCTIONS_WITHHELD_EN,
    ChatAnswer,
    condense_query_streamed,
    stream_answer,
)
from app.config import settings
from app.db import RequestSessionLocal, get_db
from app.ingestion.redaction import redact
from app.llm.gateway import active_model
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
    # The reader's own verdict on an answer, so a reload shows what they said.
    feedback: str | None = None
    feedback_citations: list[int] = []


def _response(m: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(id=m.id, role=m.role.value, content=m.content, citations=m.citations,
                               feedback=m.feedback, feedback_citations=m.feedback_citations or [])


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
    # Set for a first question only: a follow-up's answer depends on the conversation (app/chat/cache.py).
    cache_key: str | None


def _start_turn(db: Session, course_id: uuid.UUID, payload: ChatRequest, user: User) -> _Turn:
    """Saves the question and closes the session: an answer takes seconds, and must not hold a connection."""
    require_course_reader(db, db.get(Course, course_id), user)
    # A phone number or a key typed into the chat is not stored, nor sent to a model.
    message = redact(payload.message.strip())[0]
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
    key = None
    if not history:
        # The documents this person may read, as row level security shows them: a learner and a lecturer
        # see different sets, so they never share a saved answer drawn from a document one of them cannot see.
        documents = db.scalar(text(
            "SELECT md5(coalesce(string_agg(id::text, ',' ORDER BY id), '')) FROM documents"
            " WHERE course_id = :course AND status = 'embedded'"), {"course": course_id})
        key = chat_cache.cache_key(course_id, documents, active_model(), message)
    turn = _Turn(user.id, course_id, session.id, message, history, key)
    db.commit()
    db.close()
    return turn


def _user_session(user_id: uuid.UUID) -> Session:
    db = RequestSessionLocal()
    db.info["rls_user_id"] = user_id
    return db


def _search(turn: _Turn, query: str) -> tuple[list, list[float] | None, list[dict]]:
    with timing.capture_timings() as spans:
        chunks, scores = retrieve_for_chat(_user_session(turn.user_id), turn.course_id, query, top_k=8)
    return chunks, scores, spans


def _save_answer(turn: _Turn, answer: ChatAnswer, trace: dict) -> ChatMessageResponse:
    with _user_session(turn.user_id) as db:
        saved = ChatMessage(session_id=turn.session_id, role=ChatRole.assistant, content=answer.answer,
                            citations=[c.model_dump() for c in answer.citations], trace=trace)
        db.add(saved)
        db.commit()
        return _response(saved)


def _return_message(turn: _Turn) -> None:
    with _user_session(turn.user_id) as db:
        return_learner_chat(db, turn.user_id)


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _step_seconds(spans: list[dict]) -> dict[str, float]:
    seconds: dict[str, float] = {}
    for span in spans:
        seconds[span["kind"]] = round(seconds.get(span["kind"], 0.0) + span["seconds"], 4)
    return seconds


def _log_trace(trace: dict) -> None:
    """Numbers only: CloudWatch keeps the log, and the question is the reader's."""
    steps = trace.get("seconds", {})
    logger.info("chat_trace", extra={
        "route": trace["route"], "ttft_seconds": trace.get("first_token_seconds"),
        "total_seconds": steps.get("total"), "condense_seconds": steps.get("condense"),
        "rerank_seconds": steps.get("rerank"), "top_score": trace.get("top_score"),
        "prompt_tokens": trace.get("answer", {}).get("prompt_tokens"),
        "completion_tokens": trace.get("answer", {}).get("completion_tokens"),
    })


async def _answer_events(turn: _Turn) -> AsyncIterator[str]:
    """Routes a question: small talk gets a fixed reply; a first question already answered on the same
    documents gets that answer; one whose best passage is not relevant gets "not covered" without a model call;
    any other is answered from its passages. Each route leaves a trace on the saved answer."""
    started = time.perf_counter()
    trace: dict = {"seconds": {}}

    def elapsed() -> float:
        return round(time.perf_counter() - started, 3)

    async def finish(answer: ChatAnswer, route: str) -> str:
        trace["route"] = route
        trace.setdefault("first_token_seconds", elapsed())
        trace["seconds"]["total"] = elapsed()
        saved = await asyncio.to_thread(_save_answer, turn, answer, trace)
        _log_trace(trace)
        return _event("done", saved.model_dump(mode="json"))

    yield _event("session", {"session_id": str(turn.session_id)})
    try:
        if reply := small_talk(turn.message):
            yield _event("delta", {"text": reply})
            yield await finish(ChatAnswer(answer=reply, citations=[]), "small_talk")
            return
        if turn.cache_key and (saved := await asyncio.to_thread(chat_cache.get, turn.cache_key)):
            answer = ChatAnswer.model_validate(saved)
            yield _event("delta", {"text": answer.answer})
            yield await finish(answer, "cached")
            return

        condense_usage: dict = {}
        query = await condense_query_streamed(turn.message, turn.history, usage=condense_usage)
        if condense_usage:
            trace["condense"] = condense_usage
            trace["seconds"]["condense"] = condense_usage.get("seconds")
        if query != turn.message:
            trace["rewritten"] = query
        async with _retrieval_slots:
            chunks, scores, spans = await asyncio.to_thread(_search, turn, query)
        trace["seconds"].update(_step_seconds(spans))
        trace["passages"] = [{"chunk_id": str(c.id), "score": s}
                             for c, s in zip(chunks, scores or [None] * len(chunks), strict=True)]
        route = "answered"
        if scores:
            trace["top_score"] = scores[0]
            if scores[0] < settings.chat_min_rerank_score:
                # The passages found are about something else: say so without asking a model to answer from them.
                chunks, route = [], "off_topic"
        answer_usage: dict = {}
        async for part in stream_answer(turn.message, chunks, turn.history, standalone=query, usage=answer_usage):
            if isinstance(part, str):
                trace.setdefault("first_token_seconds", elapsed())
                yield _event("delta", {"text": part})
                continue
            if answer_usage:
                trace["answer"] = answer_usage
                trace["seconds"]["answer"] = answer_usage.get("seconds")
            if route == "answered" and not chunks:
                route = "no_passages"
            elif route == "answered" and part.answer in (INSTRUCTIONS_WITHHELD, INSTRUCTIONS_WITHHELD_EN):
                route = "instructions_withheld"
            elif route == "answered" and turn.cache_key:
                await asyncio.to_thread(chat_cache.put, turn.cache_key, part.model_dump())
            yield await finish(part, route)
    except Exception:
        # The 200 has already gone out, so this is the only way left to tell the reader.
        logger.exception("chat stream failed in session %s", turn.session_id)
        try:
            await asyncio.to_thread(_return_message, turn)
        except Exception:
            logger.exception("could not give back the learner's message in session %s", turn.session_id)
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
    return LatestChatResponse(session_id=session.id, messages=[_response(m) for m in session.messages])


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
    return [_response(m) for m in session.messages]


class FeedbackRequest(BaseModel):
    # None takes back what was said.
    feedback: Literal["helpful", "not_right", "wrong_citation"] | None
    note: str | None = Field(default=None, max_length=1000)
    # The numbers of the citations the reader says are wrong, as shown: [1] is the first.
    citations: list[int] = Field(default=[], max_length=50)


class OpenedRequest(BaseModel):
    citation: int = Field(ge=1, le=50)


def _own_answer(db: Session, course_id: uuid.UUID, message_id: uuid.UUID, user: User) -> ChatMessage:
    """An answer in the reader's own course chat. Row level security hides other people's messages already."""
    require_course_reader(db, db.get(Course, course_id), user)
    message = db.get(ChatMessage, message_id)
    if (message is None or message.role != ChatRole.assistant or message.session.course_id != course_id
            or message.session.module_id is not None or message.session.user_id != user.id):
        raise HTTPException(status_code=404, detail="Không tìm thấy câu trả lời.")
    return message


@router.put("/messages/{message_id}/feedback", response_model=ChatMessageResponse)
@limiter.limit("60/minute", key_func=user_or_address)
def give_feedback(
    request: Request, course_id: uuid.UUID, message_id: uuid.UUID, payload: FeedbackRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """The reader's verdict on an answer. The course's lecturers see it, with the question and the answer and
    without the reader's name (course_chat_feedback, migration 0058)."""
    message = _own_answer(db, course_id, message_id, current_user)
    if any(n < 1 or n > len(message.citations) for n in payload.citations):
        raise HTTPException(status_code=422, detail="Số trích dẫn không có trong câu trả lời này.")
    note = redact(payload.note.strip())[0] if payload.note and payload.note.strip() else None
    message.feedback = payload.feedback
    message.feedback_note = note if payload.feedback else None
    message.feedback_citations = sorted(set(payload.citations)) if payload.feedback == "wrong_citation" else []
    message.feedback_at = func.now() if payload.feedback else None
    db.commit()
    db.refresh(message)
    logger.info("ai_quality chat_feedback", extra={"feedback": payload.feedback or "withdrawn"})
    return _response(message)


@router.post("/messages/{message_id}/opened", status_code=204)
@limiter.limit("120/minute", key_func=user_or_address)
def citation_opened(
    request: Request, course_id: uuid.UUID, message_id: uuid.UUID, payload: OpenedRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """A citation the reader opened: a reader who checks a source without saying anything still tells
    something about the answer."""
    message = _own_answer(db, course_id, message_id, current_user)
    if payload.citation <= len(message.citations) and payload.citation not in message.opened_citations:
        message.opened_citations = [*message.opened_citations, payload.citation]
        db.commit()


class FeedbackItem(BaseModel):
    message_id: uuid.UUID
    answered_at: datetime
    question: str | None
    answer: str
    citations: list[dict]
    feedback: str
    note: str | None
    flagged_citations: list[int]
    feedback_at: datetime


@router.get("/feedback", response_model=list[FeedbackItem])
def list_feedback(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """The answers in this course that readers gave feedback on, newest first, without who they were: what the
    lecturer reads to improve the documents, and what scripts/chat_feedback_cases.py turns into test cases."""
    require_authored_course(db, db.get(Course, course_id), current_user)
    rows = db.execute(text("SELECT * FROM course_chat_feedback(:course)"), {"course": course_id}).mappings()
    return [FeedbackItem(**row) for row in rows]
