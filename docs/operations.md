# Running the services

Use the Compose commands in the [README](../README.md). API and worker share the same image and upload/model-cache volumes. Redis persists queue and revision state; Qdrant persists chunks and vectors. Preserve and restore both together. Do not expose these backend services directly to clients.

Health is public at `/api/v1/health`; readiness is `/api/v1/health/ready`. Readiness checks connectivity without running model calls. Under Azure it checks Search and Blob state, not a complete Service Bus exchange. A ready API can still encounter provider errors when processing a request.

Poll jobs until they succeed or fail. Failed updates leave the previous published revision available; retry the document explicitly rather than assuming automatic replay. Document deletion is an authenticated, scoped admin operation. An answer already holding a copied context cannot be recalled by a later deletion.

Azure originals retention accepts 0, 1, 7, or 30 days; 0 means retain indefinitely. Eligible originals expire when the worker runs. Indexed chunks have separate source lifecycle semantics. Job results remain pollable for 24 hours and are not a query-history feature.

Container logs use sanitized errors. Avoid logging request bodies, credentials, provider payloads, or document contents. API access logging is disabled in the supplied container commands. Configure external TLS and an application identity boundary before exposing the service beyond a trusted development environment.

Existing root-owned upload/cache volumes may need a content-preserving ownership migration for the nonroot images. Do not discard existing data as an installation shortcut. Use isolated Redis/Qdrant instances for tests, never your application data.

## Monitor local services

```sh
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs --tail=50 api worker
curl http://localhost:8000/api/v1/health/ready
```

Use the admin-only job polling endpoint for ingestion phases and sanitized failure status. The API does not expose an end-user query-history feature. Do not infer document readiness solely from container health. Service addresses differ inside Docker and on the host; keep host-side administrative/test configuration separate.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| 401 | Supply a configured bearer token; blank credentials never grant access. |
| 403 | Use admin credentials for administration, or permit the query scope through the explicit user allowlist. |
| 429 | Respect Retry-After; inspect request rate, queue capacity and provider pacing. |
| Ingestion stays queued | Check the local RQ worker, or explicitly start the manual Azure worker job. |
| Job fails while parsing | Verify format/signature, extracted text, archive bounds, page count and table dimensions. Scanned OCR is not verified. |
| Provider unavailable | Check endpoint, credential, selected model/deployment, network reachability and structured-output support. |
| Empty or refused answer | Confirm ingestion succeeded in the requested scope and that filters select the intended published source. |
| Readiness degraded | Inspect Redis/Qdrant addresses locally, or Search/Blob identity and access on Azure. |
| Permission denied on volumes | Check nonroot ownership of existing upload/model-cache data; preserve contents during migration. |

Use the [API guide](api.md) for exact HTTP and SSE error semantics. A stream ending without `done` is incomplete. Reproduce faults with synthetic inputs and avoid sharing raw provider logs or private source material.

## Maintenance

Rebuild and scan exact images after dependency updates. Validate embedding model dimensions before changing an index; use a compatible index and re-ingest documents after an embedding change. Keep state/vector backups consistent and test restoration in an isolated environment. Review credentials and scoped roles when application access changes. Optional Application Insights telemetry uses `APPLICATIONINSIGHTS_CONNECTION_STRING`; configure data handling explicitly and do not treat it as part of the default local stack.
