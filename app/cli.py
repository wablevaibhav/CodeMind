"""Command-line interface: `codemind ask|debug|explain|generate|review|learn|models|health|serve`."""

import argparse
import sys
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.exceptions import CodeMindError
from app.core.logging import configure_logging
from app.main import build_service
from app.schemas.requests import (
    ChatRequest,
    DebugRequest,
    Difficulty,
    ExplainRequest,
    GenerateRequest,
    LearningAspect,
    LearnRequest,
    ReviewRequest,
)
from app.schemas.responses import AssistantResult
from app.services import learning_service as build


def _read_code(arg: str | None) -> str | None:
    """`--code` accepts a file path or '-' for stdin."""
    if not arg:
        return None
    if arg == "-":
        return sys.stdin.read()
    path = Path(arg)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    raise SystemExit(f"error: file not found: {arg}")


def _print_result(result: AssistantResult) -> None:
    r = result.response
    print(f"\n[{r.status.value}] {r.summary or ''}".rstrip())
    print(f"\n{r.answer}\n")
    for cb in r.code_blocks:
        print(f"--- {cb.language} ---")
        print(cb.code)
        if cb.explanation:
            print(f"# {cb.explanation}")
        print()
    for title, items in (
        ("Steps", r.steps),
        ("Concepts", r.concepts),
        ("Assumptions", r.assumptions),
        ("Follow-up questions", r.follow_up_questions),
        ("Common mistakes", getattr(r, "common_mistakes", [])),
        ("Project ideas", getattr(r, "project_ideas", [])),
    ):
        if items:
            print(f"{title}:")
            for i, item in enumerate(items, 1):
                print(f"  {i}. {item}")
            print()
    for ex in getattr(r, "exercises", []):
        print(f"Exercise: {ex.prompt}")
        if ex.hint:
            print(f"  hint: {ex.hint}")
        if ex.solution:
            print(f"  solution: {ex.solution}")
    for q in getattr(r, "quiz", []):
        print(f"Quiz: {q.question}")
        for opt in q.options:
            print(f"  - {opt}")
        print(f"  answer: {q.answer}")
    if getattr(r, "learning_summary", None):
        print(f"\nSummary: {r.learning_summary}")
    for s in r.sources:
        print(f"Source: {s.title} {s.url or ''}")
    print(
        f"\n(model={result.model} mode={result.mode} attempts={result.retry.attempts}"
        f" outcome={result.retry.outcome.value} {result.elapsed_ms}ms)"
    )
    print(f"Note: {result.notice}")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", help="Ollama model (default from OLLAMA_MODEL)")
    p.add_argument("--temperature", type=float)
    p.add_argument("--json", action="store_true", help="print the full validated JSON result")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codemind", description="Local AI coding assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ask", help="Ask a programming question")
    p.add_argument("question")
    p.add_argument("--code", help="file path or '-' for stdin")
    p.add_argument("--language")
    p.add_argument("--framework")
    _add_common(p)

    p = sub.add_parser("debug", help="Analyze an error")
    p.add_argument("--error", help="file path or '-' for stdin (stack trace / logs)")
    p.add_argument("--code", help="file path")
    p.add_argument("--question")
    p.add_argument("--language")
    _add_common(p)

    p = sub.add_parser("explain", help="Explain code")
    p.add_argument("--code", required=True, help="file path or '-' for stdin")
    p.add_argument("--line-by-line", action="store_true")
    p.add_argument("--language")
    _add_common(p)

    p = sub.add_parser("generate", help="Generate code")
    p.add_argument("request")
    p.add_argument("--language")
    p.add_argument("--framework")
    p.add_argument("--tests", action="store_true", help="also generate tests")
    _add_common(p)

    p = sub.add_parser("review", help="Review code")
    p.add_argument("--code", required=True, help="file path or '-' for stdin")
    p.add_argument("--language")
    _add_common(p)

    p = sub.add_parser("learn", help="Learn a technology")
    p.add_argument("topic")
    p.add_argument("--difficulty", choices=[d.value for d in Difficulty], default="beginner")
    p.add_argument("--aspects", nargs="+", choices=[a.value for a in LearningAspect])
    _add_common(p)

    p = sub.add_parser("models", help="List installed models, or pull one explicitly")
    p.add_argument("--pull", metavar="NAME", help="download a model (explicit, may be large)")

    sub.add_parser("health", help="Check Ollama connectivity and the selected model")

    p = sub.add_parser("serve", help="Run the HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    return parser


def _run_assistant(args: argparse.Namespace, settings: Settings) -> int:
    service, ollama, _, _ = build_service(settings)
    common = {"model": args.model, "temperature": args.temperature}
    try:
        learning = False
        match args.command:
            case "ask":
                question = build.from_chat(
                    ChatRequest(
                        question=args.question,
                        code=_read_code(args.code),
                        language=args.language,
                        framework=args.framework,
                        **common,
                    )
                )
            case "debug":
                question = build.from_debug(
                    DebugRequest(
                        error_output=_read_code(args.error),
                        code=_read_code(args.code),
                        question=args.question,
                        language=args.language,
                        **common,
                    )
                )
            case "explain":
                question = build.from_explain(
                    ExplainRequest(
                        code=_read_code(args.code),
                        detail="line_by_line" if args.line_by_line else "conceptual",
                        language=args.language,
                        **common,
                    )
                )
            case "generate":
                question = build.from_generate(
                    GenerateRequest(
                        question=args.request,
                        language=args.language,
                        framework=args.framework,
                        include_tests=args.tests,
                        **common,
                    )
                )
            case "review":
                question = build.from_review(
                    ReviewRequest(code=_read_code(args.code), language=args.language, **common)
                )
            case _:  # learn
                learning = True
                kwargs = {"aspects": args.aspects} if args.aspects else {}
                question = build.from_learn(
                    LearnRequest(
                        topic=args.topic, difficulty=Difficulty(args.difficulty), **kwargs, **common
                    )
                )
        result = service.answer_learning(question) if learning else service.answer(question)
    except CodeMindError as exc:
        print(f"error [{exc.code}]: {exc.public_message}", file=sys.stderr)
        if exc.code == "model_not_found":
            print(
                "hint: ollama pull <model>   (CodeMind never downloads models itself)",
                file=sys.stderr,
            )
        return 1
    finally:
        ollama.close()
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        _print_result(result)
    return 0


def _run_models(args: argparse.Namespace, settings: Settings) -> int:
    _, ollama, selector, _ = build_service(settings)
    try:
        if args.pull:
            print(f"Pulling {args.pull} (this can take a while)...")
            ollama.pull_model(args.pull)
            print("done")
        for m in ollama.list_models():
            size = f"{m.size_bytes / 1e9:.1f} GB" if m.size_bytes else "?"
            marker = "*" if m.name == selector.get() else " "
            print(f"{marker} {m.name:<32} {size:>9}  {m.parameter_size or ''}")
        print(f"\n* = configured default ({selector.get()})")
        return 0
    except CodeMindError as exc:
        print(f"error [{exc.code}]: {exc.public_message}", file=sys.stderr)
        return 1
    finally:
        ollama.close()


def _run_health(settings: Settings) -> int:
    _, ollama, selector, _ = build_service(settings)
    try:
        version = ollama.version()
        installed = ollama.has_model(selector.get())
        print(f"Ollama {version} reachable at {ollama.base_url}")
        print(
            f"structured output: {'yes' if ollama.supports_structured_output() else 'no (JSON mode)'}"
        )
        print(f"model {selector.get()}: {'installed' if installed else 'NOT installed'}")
        if not installed:
            print(f"hint: ollama pull {selector.get()}")
        return 0 if installed else 1
    except CodeMindError as exc:
        print(f"error [{exc.code}]: {exc.public_message}", file=sys.stderr)
        return 1
    finally:
        ollama.close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = make_parser().parse_args(argv)
    settings = get_settings()
    configure_logging("WARNING" if args.command != "serve" else settings.log_level)
    if args.command == "serve":
        import uvicorn

        uvicorn.run("app.main:app_factory", factory=True, host=args.host, port=args.port)
        return 0
    if args.command == "models":
        return _run_models(args, settings)
    if args.command == "health":
        return _run_health(settings)
    return _run_assistant(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
