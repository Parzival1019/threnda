# Built-in Local AI

Threnda Community includes a managed local-AI path. Users do not need to
install Ollama, create a cloud account, select a model, or know how an
inference server works. AI actions remain optional: the application performs
no model inference unless a user explicitly requests AI assistance.

The implementation is in `threnda/app/services/ai/embedded.py` and
`threnda/app/services/ai/provider.py`. It is covered by
`tests/test_embedded_ai.py`, `tests/test_ai_provider.py`, and the document
processing tests.

## User experience

An admin opens **Settings → Local AI** and clicks **Install Local AI**. Threnda
then installs its pinned runtime and model into the application data directory.
The page shows installation state and offers a safe synthetic connection test.

The equivalent non-interactive command is:

```bash
threnda ai install
threnda ai status
```

Release maintainers can create a checksum-verified bundle for the current
platform without hand-assembling files:

```bash
threnda ai bundle --output dist/threnda-ai-bundle
```

The output contains every runtime/model/license artifact plus a pinned JSON
manifest. Archive that directory as a release asset; an offline install can
pass the extracted directory to `threnda ai install --bundle`.

The normal small distribution downloads about 1.2 GB once. An air-gapped
distribution can include the exact same files in an offline bundle and run:

```bash
threnda ai install --bundle /path/to/threnda-ai-bundle
```

No downloaded executable or model is trusted by filename alone. Every artifact
has a pinned byte size and SHA-256 digest; installation stops if either differs.
Archive paths are validated before extraction.

## Included components

- **Runtime:** llama.cpp `b10516`, distributed under MIT, with official
  prebuilt packages for macOS Apple Silicon, macOS Intel, Linux x64 and Linux
  ARM64.
- **Model:** the official Qwen `Qwen2.5-1.5B-Instruct-GGUF` Q4_K_M release,
  revision-pinned and distributed under Apache-2.0. The approximately 1.1 GB
  GGUF keeps the complete container comfortably inside the Community edition's
  lightweight 16 GB Apple-Silicon target.
- **Acceleration:** the macOS package uses Metal through llama.cpp. Linux uses
  the matching official CPU package in the Community bundle.

The production container includes the small OpenMP runtime required by the
official Linux llama.cpp binaries; users do not install it separately.

The third-party runtime license is retained inside the installed runtime, and
the checksum-pinned model license is installed beside the GGUF files. See
`THIRD_PARTY_NOTICES.md`. This does not select a license for Threnda itself;
that owner decision is still pending.

## Runtime lifecycle

The model server is not a separate user-managed application:

1. `EmbeddedAIProvider` is constructed only for an explicitly requested AI
   action.
2. `ensure_embedded_ai_running` starts the bundled `llama-server`, bound to
   `127.0.0.1` on the configured port.
3. Threnda waits for the server's `/health` endpoint before sending data.
4. The model remains available for later requests in the same application
   process.
5. The FastAPI lifespan and an `atexit` guard terminate the managed child
   process when Threnda stops.

The embedded provider calls the loopback-only OpenAI-compatible
`/v1/chat/completions` endpoint. There is no OpenAI service or API key involved;
this is only llama.cpp's local protocol.

## Safety and privacy boundaries

- The server binds to loopback, never `0.0.0.0`.
- No telemetry or cloud inference provider exists in Community.
- Untrusted document text is clipped to the configured input limit and wrapped
  in explicit data markers.
- Responses are bounded to 1 MB and parsed as JSON.
- Finding lists are capped at 100 candidates; title, excerpt, severity and
  confidence are validated before persistence.
- Model output is inert advisory text. It is never executed, interpolated into
  a query, or treated as authorization.
- Every AI candidate is visibly marked machine-generated and requires the same
  manual accept/reject flow as a heuristic candidate.
- AI never calculates the authoritative risk score, changes finding state, or
  merges records.
- A missing, failed, or slow model never fails ordinary document processing;
  heuristic candidates remain available.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `THRENDA_AI_PROVIDER` | `embedded` | `embedded`, `disabled`, or the developer compatibility option `ollama`. |
| `THRENDA_AI_DATA_DIR` | `./data/ai` | Persistent managed runtime/model directory. Compose maps this under its existing application-data volume. |
| `THRENDA_AI_BUNDLE_DIR` | *(empty)* | Optional directory containing verified offline artifacts. |
| `THRENDA_AI_EMBEDDED_PORT` | `11435` | Loopback port used by the private managed server. |
| `THRENDA_AI_STARTUP_TIMEOUT_SECONDS` | `120` | Maximum initial model-load wait, from `(0, 600]`. |
| `THRENDA_AI_TIMEOUT_SECONDS` | `30` | Inference request timeout, from `(0, 30]`. |
| `THRENDA_AI_MAX_INPUT_CHARACTERS` | `50000` | Untrusted input cap, from `1000` to `200000`. |

`THRENDA_AI_PROVIDER=disabled` retains the verified zero-I/O provider. The
application still works fully without the model files.

## Ollama compatibility

`THRENDA_AI_PROVIDER=ollama` remains available for developers who already run
Ollama. It requires `THRENDA_AI_MODEL` and a loopback-only
`THRENDA_AI_BASE_URL`; non-local URLs, embedded credentials, query strings and
fragments are rejected. Ordinary Threnda Community users do not need this mode.

## Current limits

- The managed bundle currently supports macOS arm64/x64 and Linux arm64/x64.
  Windows packaging is not yet included.
- Model installation requires around 1.5 GB of disk space and an internet
  connection unless the release includes an offline bundle.
- A containerized macOS deployment cannot use Metal from inside its Linux
  container; native macOS development gets hardware acceleration. The CPU path
  remains functional in Docker but is slower.
- There is no OCR. Image-only PDFs still require text extraction before either
  heuristic or AI-assisted review can work.
