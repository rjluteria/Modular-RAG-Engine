# Architecture

[Back to the guide](../README.md)

## Components

| Component | Responsibility |
| --- | --- |
| FastAPI (`api/`) | Authenticate requests, authorize document groups, accept uploads and serve query/job responses |
| RQ worker (`worker/`) | Run queued ingestion and query work |
| Redis | Queue state, results, rate counters, locks and published source-revision manifests |
| Qdrant | Document chunks, metadata and dense vectors |
| Core adapters (`core/adapters/`) | Parsing, embeddings, storage, retrieval, generation and queue integrations |
| Evaluation (`eval/`) | Explicit local/API capture and optional hosted judging |

## Ingestion and source identity

A document is identified by `(use_case, source_id)`. If omitted on upload, `source_id` defaults to the filename. Stable content hashes and chunk IDs prevent duplicate vectors. Identical queued/current work can be reused while its job result remains available (24 hours).

The API validates the upload before queuing it. The worker parses the file, splits documents while preserving supported table rows, rejects empty extracted content, embeds chunks, and stages a source revision. Redis publishes the complete revision only after storage succeeds. Readers copy their contexts under the publication lock. An interrupted stage leaves the previous revision visible; interrupted superseded-revision removal leaves the newly published revision visible. Replacement/deletion updates what subsequent keyword and dense retrieval can see. Back up Redis manifests and Qdrant together.

## Retrieval and generation

Dense and keyword retrieval use the same permitted, published corpus snapshot. Hybrid fusion uses stable chunk IDs so equal text from distinct sources retains its provenance. The Qdrant profile weights dense and BM25 retrieval at 0.6 and 0.4. Azure uses native Search hybrid ranking rather than these local fusion weights.

Selection keeps strong ranked hits and source diversity. It completes compact source groups only when the missing passages fit the remaining context window; large groups retain ranked passages. `top_k` is a hard maximum, not a recall guarantee. Each source group is considered once to avoid repeated work.

Retrieval happens once per answer. The same chunks are used for generation, citations and evaluation. Source identity is escaped into generation metadata. Structured output selects excerpts and qualifications from the supplied context; the server validates those selections and renders the corresponding original text and citations. One format/evidence repair is permitted using the same context.

These checks constrain output structure and provenance. They cannot establish that the model selected the right fact or interpreted it correctly. Empty context returns the standard refusal without a hosted call. Refusal with nonempty but irrelevant context is model behavior that needs separate testing.

## Startup and deployment

Readiness checks backend connectivity without initializing a collection or allocating embeddings. Models load on first use; Docker shares a persistent model cache between API and worker. Runtime dependency layers can be reused when only application code changes.

Default services run through Docker Compose. The API and worker use the same minimal nonroot image. Optional adapters remain registered but are outside the default deployment and are not all live-verified. See [Configuration](configuration.md) and [Security](../SECURITY.md).

## Azure profile

The same API and ingestion chain use Service Bus, Blob-backed job/revision state, Azure AI Search, and Foundry-hosted model deployments. Blob renewable leases coordinate source publication and worker ownership. Search visibility is confirmed before the revision is published. The worker is a manually started Container Apps Job that drains queued work; the API may scale to zero. Model clients are created per use to avoid reusing asynchronous transports across worker event loops.
