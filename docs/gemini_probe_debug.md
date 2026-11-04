# Gemini probe: HTML 400 investigation

The previous Windows probe reported `ClientError: 400 Bad Request` with a generic HTML page. This is not enough to identify the cause or prove which server generated it. We have not used the user's key, network, or Google account and cannot determine whether the response came from Google, an intermediary or a local/network configuration. Google documents model `gemini-3.1-flash-lite` for the Developer API. Their documented example uses `google.genai.Client().models.generate_content`; access for a specific account, quota and region still needs an actual request.

## Verified without network calls

With `google-genai` 2.34.0 and an `httpx.MockTransport`, the real SDK generates:

- `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent`
- Authentication is the `x-goog-api-key` HTTP header; the value is **never logged**.
- JSON payload includes a user text content and `generationConfig.temperature = 0.7`.
- HTTP 400 with an HTML body becomes an SDK `ClientError` with the original response attached. That matches the reported error format but does not establish why it happened.

The original provider relied on the SDK defaults for the base URL and API version. The new provider supplies those values explicitly and uses `vertexai=False`; the mock validates the outgoing route. This hardens configuration, but because the prior default generated the same route in our local SDK test, it is **not evidence that the user's live 400 is fixed**. Account/model access, key configuration, network proxies, service behavior or other environmental factors remain possibilities.

## Safe diagnostics

`--stage diagnose --provider gemini` makes **no HTTP request** and writes no result files. It prints whether `GEMINI_API_KEY` is present and contains whitespace, but not its value. It prints only proxy environment variable names, never proxy URLs or credentials. Environment variables are not the only way a proxy can be configured.

The paid `--stage probe` issues at most one request and does not write results or cache. On failure, diagnostics report status, the allowlisted official endpoint if visible, a sanitized classification of the response body, and whether forwarding headers are present. No raw body, query string, authentication header, arbitrary response headers, unknown hostname, key, or proxy value is printed. Headers and HTML appearance cannot prove the response origin; HTTP 400 is not retried. Never paste a key or credential into chat.

Regression tests use the real Google SDK and local mock transport to validate request method, URL, header presence, payload and retry budget, and reproduce an HTML 400 containing a fake secret. Tests verify that the fake key and proxy credentials are not printed. They do not prove live account access.

References: Google model documentation <https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite>; Google API error guide <https://ai.google.dev/gemini-api/docs/generate-content/api-errors>.
