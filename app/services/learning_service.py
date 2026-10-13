"""Turns learning / mode-specific API requests into canonical UserQuestions."""

from app.schemas.requests import (
    ChatRequest,
    DebugRequest,
    ExplainRequest,
    GenerateRequest,
    LearningAspect,
    LearnRequest,
    Mode,
    ReviewRequest,
    UserQuestion,
)
from app.services.prompt_builder import detect_mode

KNOWN_TOPICS: tuple[str, ...] = (
    "Python",
    "JavaScript",
    "TypeScript",
    "React",
    "Next.js",
    "React Native",
    "Flutter",
    "Node.js",
    "FastAPI",
    "SQL",
    "PostgreSQL",
    "Git",
    "Docker",
    "CI/CD",
    "Cloud fundamentals",
    "AI engineering",
    "Ollama",
    "LLM APIs",
    "Pydantic",
    "JSON Schema",
)  # suggestions only; any topic string is accepted

_ASPECT_GUIDE: dict[LearningAspect, str] = {
    LearningAspect.CONCEPTS: "a clear concept explanation in 'answer' and key ideas in 'concepts'",
    LearningAspect.EXAMPLES: "practical, runnable code examples in 'code_blocks'",
    LearningAspect.TUTORIAL: "a step-by-step tutorial in 'steps'",
    LearningAspect.EXERCISES: "2-4 practice exercises in 'exercises' (prompt, hint, solution)",
    LearningAspect.QUIZ: "3-5 quiz questions in 'quiz' with answers and short explanations",
    LearningAspect.COMMON_MISTAKES: "typical beginner mistakes in 'common_mistakes'",
    LearningAspect.PROJECT_IDEAS: "2-3 small project ideas in 'project_ideas'",
    LearningAspect.SUMMARY: "a brief recap in 'learning_summary'",
}


def from_chat(req: ChatRequest) -> UserQuestion:
    mode = req.mode or detect_mode(req.question, req.code)
    return UserQuestion(
        mode=mode,
        question=req.question,
        code=req.code,
        language=req.language,
        framework=req.framework,
        sources=req.sources,
        history=req.history,
        model=req.model,
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
    )


def from_debug(req: DebugRequest) -> UserQuestion:
    return UserQuestion(
        mode=Mode.DEBUG,
        question=req.question or "Find the root cause of this problem and explain how to fix it.",
        code=req.code,
        error_output=req.error_output,
        language=req.language,
        framework=req.framework,
        model=req.model,
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
    )


def from_explain(req: ExplainRequest) -> UserQuestion:
    hint = (
        "Walk through the code line by line (group trivial lines)."
        if req.detail == "line_by_line"
        else "Explain conceptually: purpose, flow and key design choices."
    )
    return UserQuestion(
        mode=Mode.EXPLAIN,
        question=req.question or "Explain this code.",
        code=req.code,
        language=req.language,
        hints=hint,
        model=req.model,
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
    )


def from_generate(req: GenerateRequest) -> UserQuestion:
    hint = "Also include unit tests for the generated code." if req.include_tests else None
    return UserQuestion(
        mode=Mode.GENERATE,
        question=req.question,
        code=req.code,
        language=req.language,
        framework=req.framework,
        hints=hint,
        model=req.model,
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
    )


def from_review(req: ReviewRequest) -> UserQuestion:
    hint = (
        "Focus on: " + ", ".join(f.value for f in req.focus) + "."
        if req.focus
        else "Cover correctness, readability, maintainability, performance and security."
    )
    return UserQuestion(
        mode=Mode.REVIEW,
        question=req.question or "Review this code.",
        code=req.code,
        language=req.language,
        hints=hint,
        model=req.model,
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
    )


def from_learn(req: LearnRequest) -> UserQuestion:
    sections = "; ".join(_ASPECT_GUIDE[a] for a in dict.fromkeys(req.aspects))
    focus = req.question or f"Teach me {req.topic}."
    return UserQuestion(
        mode=Mode.LEARN,
        question=f"Topic: {req.topic}. Difficulty: {req.difficulty.value}. {focus}",
        hints=f"Include: {sections}. Match the {req.difficulty.value} level.",
        model=req.model,
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
    )
