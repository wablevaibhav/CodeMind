# Design decisions

The technical choices that shaped CodeMind, why they were made, and what they cost.
Numbers come from this project's own measurements on one machine (31 GB RAM, 4 GB VRAM laptop
GPU) and the 12-case suite in `tests/eval/cases.json`.

---

## 1. Default to a 3B model instead of 7B

**Decision:** `qwen2.5-coder:3b` is the default; the 7B stays one setting away.

**Why:** On a 4 GB GPU the 3B model fits entirely in VRAM and generates at about 40 tokens/s,
so typical answers finish in 2 to 6 seconds. The 7B does not fit, spills onto the CPU, and took
about 28 seconds per answer on the same cases. A coding assistant that takes half a minute gets
abandoned.

| Model | Seconds per answer | Content checks passed |
| ----- | ------------------ | --------------------- |
| `qwen2.5-coder:3b` | 5.7 | 75% |
| `qwen2.5-coder:7b` | 28.1 | 92% |

**Trade-off:** Lower answer quality. The 3B model misses details and sometimes repeats a bug it was
asked about. The README says so, every answer is labelled "AI-generated, unverified", and moving to
the 7B (or a bigger GPU) is a one-line change in `.env`.

---

## 2. Validate every response with Pydantic, even when Ollama enforces the schema

**Decision:** The Pydantic model `AssistantResponse` is the single source of truth. Its JSON
Schema is generated with `model_json_schema()` and sent to Ollama as the constrained-output
`format`, and the reply is then validated again before anything is returned.

**Why:** Constrained decoding makes malformed output rare, not impossible: output can be truncated
at the token limit, older Ollama versions only support plain JSON mode, and a valid-shape object
can still break business rules (for example `needs_clarification` with no questions). Treating the
model as an untrusted component means callers only ever see validated data.

**Results:** 100% schema validity across 4 models x 12 cases. First-attempt validity was 100% for
three models and 92% for the 1.5B, where the retry pipeline recovered the rest.

**Trade-off:** Strict validation (`extra="forbid"`) can reject an otherwise good answer over an
extra field, costing a retry. `STRICT_SCHEMA=false` relaxes this. Schema validity also says nothing
about correctness, so it is reported separately from content checks and never described as
"verified".

---

## 3. Two retry policies: regenerate vs. repair, plus separate transport retries

**Decision:** Failures are handled differently by cause:

- Output is not JSON: **regenerate** with the original request plus concise feedback.
- Output is JSON but the wrong shape: send a separate **repair** prompt listing the failing
  field paths.
- Connection errors, timeouts, HTTP 429/5xx: **transport retries** with exponential backoff and
  jitter. Missing models and 4xx errors are never retried.

**Why:** Different failures need different fixes, and blind retries waste seconds on a latency
sensitive tool. Retry counts are bounded, share one request deadline, and feedback to the model
contains field paths only, never the user's values.

**Trade-off:** More moving parts and more tests (about 20 cover the retry paths). A worst-case
request takes up to three model calls, which is why latency-sensitive use relies on a model that
is right the first time.

---

## 4. Plain synchronous code with an explicit retry loop; streaming via a worker thread and SSE

**Decision:** Sync FastAPI routes and `httpx`, a small explicit retry module instead of tenacity,
and server-sent events for streaming.

**Why:** The workload is a single long call to a local model, so async buys little. The retry loop
needs to distinguish generation failures from transport failures and share a deadline, which is
clearer written out than configured. SSE needs no client library: the browser reads it with
`fetch`.

**Result:** First words appear in 0.8 to 2.2 seconds while the full validated answer arrives at
5 to 7 seconds. The streamed text is display-only; the final `result` event is always the validated
object.

**Trade-off:** Each in-flight request occupies a worker thread, so this suits one user or a small
team, not hundreds of concurrent users.

---

## 5. The server is stateless; chat history lives in the browser

**Decision:** The client sends recent turns with each request. The server stores no conversations.

**Why:** It keeps the privacy promise simple (nothing persisted server-side, no database to secure)
and keeps the API easy to reason about.

**Trade-off:** History is per browser, not synced across devices, and long chats are truncated to a
character budget before being sent to the model.

---

## 6. Model-supplied sources are discarded

**Decision:** Any `sources` in a model reply are dropped. Only sources the user supplied are
returned.

**Why:** Local models invent plausible-looking references, and the project has no retrieval layer
to verify them.

**Trade-off:** Answers never include citations unless you provide them. Adding verified retrieval
would be the next step if citations become a requirement.
