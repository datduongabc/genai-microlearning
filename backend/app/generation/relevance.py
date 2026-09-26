"""Corrective RAG: judge the passages found for an objective before anything is written from them,
and when they do not fit, rewrite the search and run it again. Runs once per module, before the
lesson text; the questions then search by their own sub-objective without a separate judgement."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.generation.common import IGNORE_EMBEDDED_INSTRUCTIONS, format_source_chunks
from app.llm.gateway import complete_structured
from app.models import Chunk

MAX_RELEVANCE_RETRY = 1

CONTENT_PURPOSE = "biên soạn bài giảng bao phủ Learning Objective và các mục tiêu thành phần của nó"


class RelevanceCheck(BaseModel):
    confidence: Literal["high", "low"]
    reason: str
    refined_query: str | None = Field(
        default=None, description="Nếu confidence=low: đề xuất query khác để truy xuất lại"
    )


_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là bộ đánh giá độ liên quan (relevance evaluator) trong pipeline Corrective
RAG. Nhiệm vụ: chấm các đoạn nguồn (source chunk) được truy xuất có ĐỦ liên
quan để {purpose} hay không.

- confidence="high": nội dung nguồn đủ cụ thể, đủ chi tiết để {purpose} có
  căn cứ vững chắc về đúng chủ đề.
- confidence="low": nguồn quá chung chung, lạc chủ đề, hoặc không đủ chi tiết
  để {purpose} có căn cứ — trong trường hợp này, đề xuất `refined_query`
  (viết lại câu truy vấn cụ thể hơn/khác cách diễn đạt) để truy xuất lại.
"""


def check_relevance(objective_text: str, chunks: list[Chunk], purpose: str = CONTENT_PURPOSE) -> RelevanceCheck:
    prompt = (
        f"LEARNING OBJECTIVE: {objective_text}\n\n"
        f"{format_source_chunks(chunks)}\n\n"
        f"Chấm độ liên quan của các đoạn nguồn trên với Learning Objective."
    )
    return complete_structured(prompt, RelevanceCheck, system_instruction=_INSTRUCTION.format(purpose=purpose))


@dataclass
class CorrectiveResult:
    chunks: list[Chunk]
    confidence: Literal["high", "low"]
    retries: int


def corrective_retrieve(
    objective_text: str,
    chunks: list[Chunk],
    re_retrieve: Callable[[str], list[Chunk]],
    purpose: str = CONTENT_PURPOSE,
    max_retry: int = MAX_RELEVANCE_RETRY,
) -> CorrectiveResult:
    """Judges `chunks`; while the judge says low and offers a better query (and the budget lasts),
    searches again with it and judges what was found together with what was already there.
    Once the budget is spent it goes on with the best it has instead of blocking the module."""
    check = check_relevance(objective_text, chunks, purpose)
    retries = 0
    while check.confidence == "low" and check.refined_query and retries < max_retry:
        retries += 1
        fresh = re_retrieve(check.refined_query)
        seen = {chunk.id for chunk in fresh}
        chunks = [*fresh, *(chunk for chunk in chunks if chunk.id not in seen)]
        check = check_relevance(objective_text, chunks, purpose)
    return CorrectiveResult(chunks=chunks, confidence=check.confidence, retries=retries)
