# API guide

[Back to the guide](../README.md)

The base path is `/api/v1`. Interactive schemas are served at `/docs`.

| Method | Path | Credential |
| --- | --- | --- |
| POST | `/api/v1/ingest` | Admin |
| GET | `/api/v1/jobs/{job_id}` | Admin |
| POST | `/api/v1/query` | User or admin |
| POST | `/api/v1/query/async` | Admin |
| DELETE | `/api/v1/documents/{source_id}?use_case=...` | Admin |
| PUT | `/api/v1/configuration` | Admin; Azure shared state |
| GET | `/api/v1/health` | Public |
| GET | `/api/v1/health/ready` | Public |

All document and query requests require `Authorization: Bearer <token>`. User tokens permit synchronous and streaming queries. Admin tokens also permit uploads, deletion, asynchronous queries, and job polling. These are two shared service credentials, not a multi-user identity system. Health endpoints are public. Missing credentials grant no access.

Supply tokens to curl through local environment variables, without putting literal secrets into scripts or browser bundles:

```sh
curl http://localhost:8000/api/v1/ingest \
  -H "Authorization: Bearer $API_ADMIN_TOKEN" \
  -F file=@sample.txt -F use_case=synthetic -F source_id=shipping-guide

curl http://localhost:8000/api/v1/jobs/JOB_ID \
  -H "Authorization: Bearer $API_ADMIN_TOKEN"

curl http://localhost:8000/api/v1/query \
  -H "Authorization: Bearer $API_USER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"What is the shipping limit?","use_case":"synthetic","top_k":4,"filters":{"source_id":"shipping-guide"}}'
```

Uploads return HTTP 202 with a job ID and polling URL. Job states are queued, running, succeeded, and failed, with a sanitized failure and initialization, parsing, chunking, embedding/storage, and completion phases. The upload limit defaults to 20 MiB. PDF, DOCX, TXT, HTML, and CSV are supported; unsupported, empty, oversized, and invalid-signature files are rejected. Parser failures become failed jobs. Table expansion is limited to 100,000 cells and PDFs to 1,000 pages. PDF parsing extracts existing text and ruled tables. Table rows retain column labels, units and nearby qualifications; HTML row/column spans and marked Word header rows are preserved. Complex borderless PDF layouts and scanned pages are not guaranteed to reconstruct as tables. Scanned documents and OCR are **not supported or verified** by this release. Complete table rows are retained even when they exceed the embedding model window. The default embedding model truncates inputs beyond 256 tokens; keyword retrieval and stored generation text retain the full row. This is a dense-retrieval limitation for unusually long rows.

The document identity is `(use_case, source_id)`. A successful upload replaces that identity's chunks; repeated identical uploads reuse pending/current completed work within the 24-hour job-result retention window. Content hashes and deterministic chunk IDs prevent accumulating duplicate vectors. A changed upload replaces stale chunks and keyword retrieval loads current storage on the next query. Replacement stages a complete revision before atomically publishing its visibility in Redis. Queries copy contexts under the publication lock; interrupted staging leaves the previous revision visible, and interrupted superseded-revision removal leaves the complete published revision visible. Redis manifests and Qdrant data must be backed up/restored together. Re-upload the document after a failed/interrupted update. Deleting a document is an admin request to `DELETE /api/v1/documents/SOURCE_ID?use_case=synthetic`. Deletion is idempotent.

See [Architecture](architecture.md) for context selection, source validation and generation behavior. `top_k` is 1–50 (default 8); equality filters accept string, integer and boolean values. Empty context produces the standard refusal.

## Frontend and streaming

Configure `CORS_ORIGINS` as a JSON list of allowed origins, for example `["http://localhost:3000"]`. Keep admin credentials on a trusted backend. A frontend can call your own backend, which supplies the authorized user credential:

```js
const response = await fetch('/backend/rag/query', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ query: 'What is the shipping limit?', use_case: 'synthetic', stream: true })
});
// Read response.body with a streaming UTF-8 decoder and an SSE parser.
```

`POST /api/v1/query` with `stream: true` returns `text/event-stream`. Each event has `event: NAME` and one JSON `data:` line, terminated by a blank line. Events arrive as `sources` (`{"sources":[...]}`), zero or more `token` events (`{"text":"..."}`), and `done` (`{}`). A generation failure emits `error` with a sanitized code/message and closes the stream without `done`. Failures before streaming starts use an HTTP error. The answer is buffered until evidence validation completes, then emitted as a `token` payload; source events arrive first. The API does not forward unvalidated provider tokens. JSON framing preserves quotes and newlines. A frontend must discard partial answers or label them incomplete after `error` or a connection close without `done`. Streaming source entries contain the source text and `generation_context`, the exact tagged context used for generation and evaluation. Regular query responses expose source metadata through OpenAPI schemas.

With Redis, requests use a shared Redis rate counter per credential (60 queries/minute; uploads use a stricter 20-request threshold on that same counter). HTTP 429 includes `Retry-After`. The Azure state adapter maintains route-specific counters. Provider model options and shared pacing are described in [Configuration](configuration.md).

## Background queries

`POST /api/v1/query/async` accepts the query request fields and returns HTTP 202 with `data.job_id`, `data.status` and `data.poll_url`. Poll the admin-only job endpoint for `data.result` or a sanitized `data.error`. Use the synchronous query endpoint with `stream: true` for SSE; asynchronous queries return a stored result rather than a live stream.

## Errors

Missing/invalid credentials return 401; disallowed scopes or admin-only operations return 403. Invalid request fields return 422; oversized files return 413; unsupported formats return 415. Provider/retrieval failures can return 503, and rate limits return 429. Check the HTTP status and response detail rather than assuming every failure shares one JSON shape. Streaming failures after headers use an `error` event and omit `done`.

## Shared Azure settings

`PUT /api/v1/configuration` is admin-only and accepts:

```json
{"approved_use_cases":["example"],"originals_retention_days":0}
```

Retention supports 0 (forever), 1, 7 or 30 days. Changes require the shared Azure state adapter. Expiry only removes eligible originals, not indexed content. Original expiry is applied when the worker runs; it is not an independently scheduled service. Completed Azure job results expire after 24 hours; this is job polling, not a query-history API.
