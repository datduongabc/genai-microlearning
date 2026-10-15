"""Synthetic model catalog and evaluation packets shared by judge tests."""
from app.generation.evaluation import (
    DimensionScore,
    ExampleInput,
    ModuleContentInput,
    ModuleEvaluation,
    QuizQuestionInput,
    SlideInput,
)

GATEWAY = [
    {"model_name": "gemini-fast", "litellm_params": {"model": "gemini/gemini-flash-lite-latest"}},
    {"model_name": "claude-pro", "litellm_params": {"model": "anthropic/claude-sonnet-5"}},
    {"model_name": "groq-pro", "litellm_params": {"model": "groq/openai/gpt-oss-120b"},
     "model_info": {"max_input_tokens": 128000}},
    {"model_name": "openai-fast", "litellm_params": {"model": "openai/gpt-4o-mini"}},
]


def _packet() -> ModuleContentInput:
    question = QuizQuestionInput(stem="Hỏi", choices=["A", "B", "C", "D"], correct_index=1, explanation="Vì B")
    return ModuleContentInput(
        anon_id="", topic="Chủ đề", bloom_level="understand", learning_objective="Giải thích X.",
        sub_objectives=["x"], content_markdown="## X", slides=[SlideInput(title="T", bullets=["b"])],
        examples=[ExampleInput(context="c", analysis="a")], quiz=[question], practice=question,
        source_chunks=["Nguồn."],
    )



def _evaluation(anon_id: str, score: int = 4) -> ModuleEvaluation:
    dim = DimensionScore(score=score, justification="ổn")
    return ModuleEvaluation(
        anon_id=anon_id, accuracy=dim, completeness=dim, consistency=dim, hallucination_score=dim,
        slide_quality=dim, quiz_quality=dim, bloom_fidelity=dim, query_alignment=dim, overall_notes="",
    )
