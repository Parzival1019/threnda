# Optional local AI (Milestone 6)

This document describes `threnda/app/services/ai/provider.py` as actually
implemented, per CLAUDE.md's "Optional local AI only" / "No external LLM
requirement" rules. Covered by `tests/test_ai_provider.py` and the AI
opt-in tests in `tests/test_documents_processing.py`.

**AI is disabled by default and entirely optional.** Threnda runs fully
offline, with every Milestone 1–6 feature working normally, whether or not
AI is ever configured. There is no cloud LLM provider, and there will not
be one — the only supported backend is a local [Ollama](https://ollama.com)
instance you already run yourself.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `THRENDA_AI_PROVIDER` | `disabled` | `disabled` or `ollama`. |
| `THRENDA_AI_MODEL` | *(empty)* | Required (non-blank) when `ollama` is selected. |
| `THRENDA_AI_BASE_URL` | `http://127.0.0.1:11434` | Must resolve to `127.0.0.1`/`localhost`/`::1`; no credentials, query string, or fragment. Enforced at config-load time (`threnda.app.core.config.Settings.validate_local_ai_url`) *and* again inside `OllamaAIProvider.__init__` — defense in depth, not redundancy for its own sake. |
| `THRENDA_AI_TIMEOUT_SECONDS` | `5` | `(0, 30]`. |
| `THRENDA_AI_MAX_INPUT_CHARACTERS` | `50000` | `[1000, 200000]`. Untrusted document text is clipped to this length before being sent to the model. |

See `.env.example` for the exact placeholder values.

## `AIProvider` interface

```python
class AIProvider(Protocol):
    def summarize_finding(self, text: str) -> AIResult: ...
    def explain_finding(self, text: str) -> AIResult: ...
    def suggest_remediation(self, text: str) -> AIResult: ...
    def extract_findings_from_text(self, text: str) -> tuple[AICandidate, ...]: ...
    def compare_findings(self, left: str, right: str) -> AIResult: ...
```

`AIResult.machine_generated` and `AICandidate.machine_generated` are always
`True` — callers never need to guess whether output came from a model; see
`docs/pentest-and-ai.md` for how this becomes a visible "AI GENERATED"
label and never bypasses human review.

## `DisabledAIProvider` — the default

Every method raises `AIUnavailableError` immediately, before anything else
runs. **Zero I/O** — `tests/test_ai_provider.py` verifies this by patching
`urlopen` and asserting it is never called for any of the five methods.
This is what makes "AI disabled" a real, verifiable guarantee rather than a
policy statement: there is no code path in `DisabledAIProvider` that can
reach the network.

## `OllamaAIProvider` — the only network-capable provider

`threnda/app/services/ai/provider.py::OllamaAIProvider`. A minimal client
for Ollama's `/api/generate` endpoint, using only Python's standard-library
`urllib` — no HTTP client dependency was added for this.

- **Localhost enforcement**: the constructor re-validates the base URL's
  hostname even though `Settings` already did, so calling
  `OllamaAIProvider(...)` directly (e.g. in a test, or from future code)
  can never bypass the restriction by skipping `Settings`.
- **Prompt construction**: untrusted document text is wrapped in an
  explicit instruction to the model plus `---BEGIN/END UNTRUSTED DATA---`
  markers around the text itself (`OllamaAIProvider._generate`). This is a
  hint to a cooperative model, not a security boundary — the real boundary
  is that *nothing* the model returns is trusted without the schema
  validation described next, and no returned text is ever executed,
  templated, or used to build a query — only ever copied into a labelled,
  advisory string field.
- **Response validation**: every response is schema-checked before use.
  `_generate` bounds the read to `MAX_RESPONSE_BYTES` (1,000,000) and
  rejects anything larger, rejects non-JSON, and rejects a missing
  `response` envelope key. `_text_result` requires a `{"text": str}` shape
  with non-empty, non-oversized (&le;20,000 char) text.
  `extract_findings_from_text` requires `{"findings": [...]}`, caps the
  list at 100 items, and validates every item's `title` (non-empty,
  &le;500 chars), `excerpt` (non-empty, &le;5000 chars), and `confidence`
  (a number in `[0, 1]`) — any violation raises `AIUnavailableError` for
  the whole call rather than silently coercing bad data.
- **Failure handling**: `HTTPError`, `URLError`, `TimeoutError`, and any
  other `OSError` from the transport are all reported as one
  `AIUnavailableError` ("Local Ollama is unavailable or timed out") —
  callers only ever need to handle one exception type, and Threnda keeps
  functioning normally (heuristic candidates are unaffected) whether Ollama
  is running or not.
- **Misconfiguration** (e.g. a blank `THRENDA_AI_MODEL` with `ollama`
  selected) is also reported as `AIUnavailableError`, via
  `build_ai_provider` — never a raw `ValueError` reaching a route handler.
  See `tests/test_ai_provider.py::test_build_ai_provider_wraps_misconfiguration_as_ai_unavailable`.

## Testing strategy

Every `OllamaAIProvider` test in `tests/test_ai_provider.py` patches
`threnda.app.services.ai.provider.urlopen` — there is no real Ollama
dependency anywhere in the test suite or in CI. Covered: happy path for
all five methods, oversized/invalid/non-JSON/missing-key responses,
timeouts and transport errors, the 100-candidate cap, per-candidate schema
validation, input clipping at `THRENDA_AI_MAX_INPUT_CHARACTERS`, localhost
enforcement (including sneaky non-localhost hostnames like
`127.0.0.1.evil.test` and `localhost.evil.test`), and that a
prompt-injection-shaped input or response is only ever treated as inert,
schema-validated string data.

## What this milestone does not add

- No cloud LLM provider, and no configuration path to one — `Settings`
  rejects any `THRENDA_AI_BASE_URL` that isn't loopback.
- No AI-driven risk scoring — `docs/risk-scoring.md`'s engine remains
  entirely deterministic and rule-based; AI never touches
  `Exposure.risk_score`.
- No AI-driven auto-acceptance of candidates — see `docs/pentest-and-ai.md`,
  "Mandatory human review".
- No telemetry of any kind sent about AI usage, on or off.
