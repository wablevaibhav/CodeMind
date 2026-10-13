# CodeMind

A privacy-focused, **local-first AI coding assistant**. CodeMind runs open-source models through
[Ollama](https://ollama.com), and returns **schema-validated JSON** instead of free-form text.
Invalid model output triggers bounded, validation-guided retries; if recovery fails you get a
structured error, never malformed data.

- No cloud LLM APIs, no paid services, no telemetry. Prompts and code stay on your machine.
- Six modes: **ask, debug, explain, learn, generate, review**, all on one shared pipeline.
- CLI + FastAPI (Swagger UI at `/docs`).
- Pydantic v2 models are the single source of truth for request/response contracts and JSON Schema.

## Architecture

```
CLI / FastAPI routes
        │  request models (Pydantic, extra=forbid)
        ▼
learning_service  ── maps each endpoint onto a canonical UserQuestion (mode + hints)
        ▼
assistant_service ── model selection, deadline, logging/metrics, result envelope
        ▼
retry_service (StructuredGenerator)
   ├─ prompt_builder      system prompt + JSON Schema + mode template (+ feedback on retry)
   ├─ ollama_client       /api/chat with `format: <JSON Schema>`; errors → typed exceptions
   ├─ response_validator  extract JSON → Pydantic validate → sanitized ValidationFailure
   └─ transport retries   backoff + jitter for connection/timeout/429/5xx only
```

```
app/
  main.py                 app factory, exception handlers, correlation-ID middleware
  cli.py                  `codemind` command
  api/routes/             health, models, chat, code_tools (debug/explain/generate/review), learning
  core/                   config (pydantic-settings), exceptions, structured logging + metrics
  schemas/                requests.py, responses.py (AssistantResponse…), errors.py (retry/failure)
  services/               ollama_client, prompt_builder, response_validator, retry_service,
                          assistant_service, learning_service
  prompts/*.txt           system, chat, debug, explain, learn, generate, review, retry, repair
  utils/                  json_parser, timing
  evaluation.py           evaluation harness (schema validity vs. property checks)
tests/unit  tests/integration  tests/eval  scripts/run_eval.py
```

## Prerequisites

- Python **3.12+** (developed on 3.14), Windows / macOS / Linux
- [Ollama](https://ollama.com/download) **0.5.0+** for schema-constrained output. Older versions
  work through plain JSON mode (CodeMind detects this automatically).
- Disk/RAM for a model (see *Choosing a model*)

## Install

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS / Linux:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Ollama and models

Install Ollama, make sure it is running (`ollama serve`, or the desktop app), then **pull a model
manually**. CodeMind never downloads models on its own at startup.

```bash
ollama pull qwen2.5-coder:7b
```

### Choosing a model

| Machine                            | Suggested model            | Notes                                  |
| ---------------------------------- | -------------------------- | -------------------------------------- |
| ≤ 8 GB RAM, no GPU                 | `qwen2.5-coder:1.5b`       | Fast, weakest answers                  |
| 8–16 GB RAM or 4 GB VRAM           | `qwen2.5-coder:3b`         | Fits fully on a 4 GB GPU               |
| 16+ GB RAM (**default**)           | `qwen2.5-coder:7b`         | Good quality/size balance              |
| 16–32 GB RAM or 12+ GB VRAM        | `qwen2.5-coder:14b`        | Noticeably better reasoning            |
| Large GPU / workstation            | `qwen2.5-coder:32b`        | Best local coding quality here         |

Any instruction-following Ollama model works (`llama3.2`, `deepseek-coder-v2`, …). Larger models
produce valid JSON more often and need fewer retries.

### Switching models

- Persistently: set `OLLAMA_MODEL` in `.env`.
- Per request: `--model` (CLI) or `"model"` in the request body.
- At runtime (in-memory, resets on restart): `POST /models/select {"model": "..."}`. It only
  validates that the model is installed; it does not download it.

## Run

CLI (PowerShell or bash):

```bash
codemind health                                   # Ollama reachable? model installed?
codemind models                                   # installed models
codemind models --pull qwen2.5-coder:3b           # explicit download
codemind ask "How do I read a file line by line in Python?"
codemind debug --error trace.txt --code app.py
codemind explain --code app.py --line-by-line
codemind generate "a FastAPI endpoint that returns server time" --language python --tests
codemind review --code app.py
codemind learn "Docker" --difficulty beginner --aspects concepts exercises quiz
codemind ask "..." --json                         # full validated JSON
codemind serve --port 8000                        # HTTP API
```

`--code -` / `--error -` read from stdin. Without installing the entry point you can use
`python -m app.cli ...`.

**Web UI:** after `codemind serve`, open <http://127.0.0.1:8000/> for a browser interface with all
six modes, model picker, copy buttons, and the retry/validation details of each response. It is a
single static HTML file served by the API (no build step, no external assets); model output is
rendered as text only, under a strict Content-Security-Policy.

API docs: <http://127.0.0.1:8000/docs>.

| Endpoint            | Purpose                                      |
| ------------------- | -------------------------------------------- |
| `GET /health`       | Application health                           |
| `GET /health/ollama`| Ollama connectivity, version, model status   |
| `GET /models`       | Installed models and the selected one        |
| `POST /models/select` | Select an installed model                  |
| `POST /chat`        | Answer a question (mode auto-detected)       |
| `POST /debug`       | Analyze an error / stack trace / code        |
| `POST /explain`     | Explain code                                 |
| `POST /generate`    | Generate code (optionally with tests)        |
| `POST /review`      | Review code                                  |
| `POST /learn`       | Structured lesson, exercises, quiz           |
| `GET /metrics`      | Counters only (no user content)              |

```bash
curl -s http://127.0.0.1:8000/chat -H "content-type: application/json" \
  -d '{"question": "How do I square every number in a list?", "language": "python"}'
```

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/chat -Method Post -ContentType application/json `
  -Body '{"question": "How do I square every number in a list?", "language": "python"}'
```

Response (shape; content depends on the model):

```json
{
  "request_id": "3f9c1d0a52be",
  "mode": "ask",
  "model": "qwen2.5-coder:7b",
  "response": {
    "status": "success",
    "intent": "coding_question",
    "answer": "Use a list comprehension to build a new list of squares.",
    "summary": null,
    "code_blocks": [
      {"language": "python", "code": "squares = [x * x for x in numbers]", "explanation": null}
    ],
    "steps": [], "concepts": ["list comprehension"], "assumptions": [],
    "follow_up_questions": [], "sources": [], "error": null
  },
  "schema_valid": true,
  "ai_generated": true,
  "verified": false,
  "notice": "AI-generated by a local model. Schema-valid, but not independently verified: ...",
  "retry": {"attempts": 1, "max_attempts": 3, "transport_retries": 0,
            "outcome": "success", "elapsed_ms": 4210, "history": [...]},
  "elapsed_ms": 4212
}
```

On failure the body is `{"error": {"code", "message"}, "request_id", "retry"}` with a suitable
status: `422` invalid request, `404` model not installed, `502` model failed / retries exhausted,
`503` Ollama unreachable, `504` timeout or deadline. Stack traces, paths and raw model output are
never returned.

## How structured output works

The contract is `AssistantResponse` in `app/schemas/responses.py`. The JSON Schema sent to the
model is generated from that class:

```python
from app.schemas.responses import AssistantResponse

schema = AssistantResponse.model_json_schema()
```

1. The schema is passed to Ollama as `format` (constrained decoding) **and** embedded in the system
   prompt. On Ollama < 0.5 CodeMind falls back to `format: "json"`.
2. The reply is parsed as JSON (a stray markdown fence or surrounding prose is tolerated).
3. It is validated with Pydantic (`extra="forbid"`, enum/type/length constraints, plus rules such as
   *needs_clarification requires follow-up questions*). `STRICT_SCHEMA=false` drops unknown fields
   instead of failing.
4. Only a validated object reaches the caller. `sources` from the model are **discarded**; only
   sources you supply are returned.

`schema_valid: true` means *the shape is right*. It says nothing about whether the code is correct.
Every result carries `ai_generated: true` and `verified: false`; run and review generated code.

## Retries and self-correction

Two independent policies (`app/services/retry_service.py`):

| Policy         | Triggers                                              | Behaviour                                  |
| -------------- | ----------------------------------------------------- | ------------------------------------------ |
| **Generation** | invalid JSON, empty reply, schema violation           | up to `MAX_GENERATION_RETRIES` (default 2) |
| **Transport**  | connection failure, timeout, HTTP 429/500/502/503/504 | up to `MAX_TRANSPORT_RETRIES` (default 2), exponential backoff with jitter capped at `MAX_RETRY_BACKOFF_SECONDS` |

- Not valid JSON → **regenerate**: original request + truncated rejected output + concise feedback.
- Valid JSON, wrong shape → **repair**: a separate prompt that lists the field paths that failed
  and asks to fix them without changing the content.
- Feedback lists field paths and messages only (never submitted values), capped at
  `MAX_FEEDBACK_CHARS`; previous output is truncated.
- Never retried: invalid requests, missing model, other 4xx. Retries stop at
  `REQUEST_DEADLINE_SECONDS` and on cancellation.
- Exhaustion → `502 generation_failed` with `retry` metadata (attempt count, per-attempt kind and
  failure category, timings).

## Configuration

See `.env.example`. Settings are validated at startup (bad values fail fast). Key ones:
`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS`, `OLLAMA_NUM_CTX`,
`MAX_GENERATION_RETRIES`, `MAX_TRANSPORT_RETRIES`, `MAX_RETRY_BACKOFF_SECONDS`,
`REQUEST_DEADLINE_SECONDS`, `DEFAULT_TEMPERATURE`, `MAX_OUTPUT_TOKENS`, `STRICT_SCHEMA`, `LOG_LEVEL`.

## Tests, lint, evaluation

```bash
pytest                      # unit tests: no Ollama or model needed (Ollama is mocked)
pytest -m integration       # optional: uses your local Ollama + configured model; skips if absent
ruff check . && ruff format --check .
python scripts/run_eval.py  # evaluation suite against your local model
```

`run_eval.py` reports **schema validity** and **heuristic property checks** separately; passing
properties are not proof of technical correctness.

## Privacy and local data

- All inference goes to `OLLAMA_BASE_URL` (default `localhost`). There are no other outbound calls.
- Logs are structured JSON and contain request ID, model, mode, latency, attempts, validation
  result and failure category. **Prompts, code and raw model output are not logged.**
- `/metrics` exposes counters only. Nothing is persisted to disk by CodeMind.
- Keep `.env` out of version control (it is git-ignored). Bind the API to `127.0.0.1` (default); it
  has no authentication, so do not expose it publicly.

## Known limitations

- Only one model backend (Ollama); no retrieval, so `sources` are only what you provide.
- Small models may exceed context or need several retries; JSON schema validity ≠ correctness.
- Sync request handling: one long inference occupies a worker thread. Client disconnects are not
  detected mid-inference (the deadline still applies).
- The web UI is deliberately minimal: no conversation history or streaming.
- `POST /models/select` is in-memory per process.

## Troubleshooting

| Symptom                                      | Fix                                                                 |
| -------------------------------------------- | ------------------------------------------------------------------- |
| `ollama_unavailable`                         | Start Ollama (`ollama serve`); check `OLLAMA_BASE_URL`              |
| Slow first connect on Windows                | Use `http://127.0.0.1:11434` (avoids IPv6 `localhost` fallback delay) |
| `model_not_found`                            | `ollama pull <model>` or fix `OLLAMA_MODEL`                         |
| `inference_timeout` on first request         | Model is loading; raise `OLLAMA_TIMEOUT_SECONDS`                    |
| Frequent `generation_failed`                 | Use a larger model, lower `DEFAULT_TEMPERATURE`, raise `MAX_OUTPUT_TOKENS` (truncated JSON is invalid) |
| Answers ignore long code                     | Raise `OLLAMA_NUM_CTX` (needs more RAM)                             |
