# Provider integration notes

AMCA owns its asynchronous `ModelAdapter` contract. Provider SDKs sit behind
that boundary; importing or running the offline demo does not read a key or
make a network request.

## Implemented adapters

- OpenAI: official async SDK and Responses API.
- Anthropic: official async SDK and Messages API.
- Kimi/Moonshot: OpenAI-compatible Chat Completions, including the documented
  K2.5/K2.6 temperature and completion-token mapping.
- Gemini: REST generate and streaming contracts.
- Ollama: local `/api/chat`.
- Generic OpenAI-compatible endpoints plus named loopback defaults for
  llama.cpp, vLLM, and SGLang.

These adapters have offline protocol tests. R1 validates a real Ollama
`qwen3:1.7b` Solo run. Bounded OpenAI R2 calls validate authentication,
Responses schema, structured parsing, streaming, usage/cost settlement,
permission scoping, credential non-persistence, and cancellation after the
bounded-close correction. Native Anthropic R2 calls validate the Messages
schema, structured parsing, complete streaming and usage, request-scoped
cancellation, zero retries, and credential non-persistence. The OpenAI and
Anthropic R2 slices are complete. Native Kimi/Moonshot R2 calls additionally
validate K2.6's request mapping, complete streaming/usage, structured parsing,
and bounded cancellation. A bounded native Gemini attempt validated safe
failure reporting and zero retries but returned HTTP 404 twice before streaming;
its live gate is skipped and remains incomplete. Other cloud and local-compatible endpoints remain
UNVALIDATED and are not production-certified.

The OpenAI SDK client disables automatic retries, and asynchronous stream close
has a short independent bound so cancellation cannot hold AMCA indefinitely.
See [`validation/r2-openai.md`](validation/r2-openai.md).
See [`validation/r2-anthropic.md`](validation/r2-anthropic.md).
See [`validation/r2-kimi.md`](validation/r2-kimi.md).
See [`validation/r2-gemini.md`](validation/r2-gemini.md).

## Credentials

Cloud adapters resolve keys only at call time from an explicit value, process
environment, or the credential-store abstraction. Supported environment names
are:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `MOONSHOT_API_KEY`
- `GEMINI_API_KEY`

On Windows, `amca credentials set/status/delete --provider PROVIDER` uses
Windows Credential Manager without echoing the secret. Credential-like keys are
rejected recursively before event and SQLite persistence. Never place secrets
in project definitions, prompts, benchmark files, artifacts, logs, or Git.

## Privacy, endpoints, and cost

OFFLINE rejects cloud adapters before a call. Local-compatible endpoints must
be loopback unless the caller explicitly opts into remote behavior. Provider
HTTP adapters and the Ollama inventory/measurement path reject redirects by
default so a validated loopback request cannot be redirected off-machine.

PRIVATE_HYBRID requires an exact reviewed excerpt, matching one-use approval,
and content-free transmission audit. FULL_FRONTIER must be selected explicitly.
Cloud execution also requires configured pricing and a finite task budget;
unknown pricing is rejected.

Model identifiers and prices are configuration, not source constants. Copy
`config/pricing.example.json`, record an effective date, and enter values from
current official pricing pages before any approved paid run.

## Failure boundary

Adapters normalize output, usage, latency, provider request identity, and
errors. They do not choose collaboration mode, authorize transmission, accept
claims, mutate canonical state, or declare goal success. Retry/substitution
belongs to the kernel so it can be logged and budgeted; AMCA never silently
retries a successfully completed paid call.
