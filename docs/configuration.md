# Configuration

[Back to the guide](../README.md)

Use `.env.example` as the starting point for `.env.local`. Settings are defined in [`core/config.py`](../core/config.py); OS environment values take precedence over local dotenv files. Compose injects `.env.local` and overrides service addresses and upload storage for containers. Do not use shell `source` to load this file: its JSON values are dotenv configuration, not a shell script.

## Default Docker profile

The supplied environment example selects the following defaults; they are not universal requirements.

### Models

| Role | Model ID | Setting |
| --- | --- | --- |
| Generation | `nvidia/nemotron-3-super-120b-a12b` | `NVIDIA_NIM_MODEL` |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | `SENTENCE_TRANSFORMERS_MODEL` |
| Evaluation judge | `nvidia/nemotron-3-super-120b-a12b` | `NVIDIA_JUDGE_MODEL` |

Generation and judging use NVIDIA’s hosted API with `NVIDIA_API_KEY`; embeddings run locally and download their weights on first use. Change generation and evaluation model IDs independently. Model options must match the selected model; changing a model requires new accuracy validation.

| Setting | Default | Meaning |
| --- | --- | --- |
| `NVIDIA_STRUCTURED_OUTPUT_METHOD` | `json_schema` | Strict answer schema; `json_mode` is available for compatible JSON-only models |
| `NVIDIA_GENERATION_OPTIONS` | `{"reasoning_effort":"low","reasoning_budget":1024}` | Generation options |
| `NVIDIA_JUDGE_OPTIONS` | `{"chat_template_kwargs":{"enable_thinking":false}}` | Judge options |
| `MAX_MODEL_INPUT_TOKENS` | `16000` | Maximum input size per Azure model request, including structured schema |
| `MAX_MODEL_OUTPUT_TOKENS` | `2048` | Maximum output size per Azure model request |
| `MODEL_TIMEOUT_SECONDS` | `120` | Per-provider client timeout |
| `MODEL_MAX_RETRIES` | `3` | SDK retry setting |
| `PROVIDER_MIN_INTERVAL_SECONDS` | `15` | Shared request spacing |
| `PROVIDER_WAIT_TIMEOUT_SECONDS` | `600` | Bounded wait for shared capacity |
| `PROVIDER_COOLDOWN_SECONDS` | `90` | Cooldown after exhausted rate-limit retries |

See the [provider’s inference reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer) for supported options. Use `{}` for models that do not support these optional parameters. The shared Redis provider gate coordinates API, worker and evaluation traffic. Retries are bounded and honor supported retry headers.

## Access and browser origins

- `API_ADMIN_TOKEN` and `API_USER_TOKEN`: different random service credentials. Empty credentials grant no access; equal nonempty values are rejected.
- `API_USER_USE_CASES`: explicit JSON list of groups the user token can query. Empty means deny all user queries. Under Azure, an admin update to shared `approved_use_cases` overrides the environment allowlist. Admin access remains privileged across groups.
- `CORS_ORIGINS`: JSON list of permitted browser origins, such as `["http://localhost:3000"]`. Default: `[]`. This is separate from authorization.

Keep keys/tokens on your trusted backend and out of browser bundles, commits, images and shared logs. Hosted generation/evaluation sends document excerpts to the configured provider. For additional safeguards and residual risks, see [SECURITY.md](../SECURITY.md).

## Retrieval, parsing and storage

| Setting | Default |
| --- | --- |
| `RETRIEVAL_TOP_K` | `8` (per-query override: 1–50) |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `120` |
| `HYBRID_DENSE_WEIGHT` / `HYBRID_BM25_WEIGHT` | `0.6` / `0.4` (Qdrant profile only) |
| `RERANKER_ENABLED` | `false` |
| `MAX_UPLOAD_BYTES` | `20971520` (20 MiB) |
| `QDRANT_COLLECTION` | `rag_chunks` |

Defaults select `structured`, `sentence_transformers`, `qdrant`, `nvidia_nim` and `rq` for parser, embedder, store, LLM and queue providers. Changing an adapter can change the supported operations and credential requirements; some nondefault operations explicitly return unsupported-operation errors.

The structured parser limits table expansion to 100,000 cells and PDFs to 1,000 pages. Complete stored rows can exceed the embedding model’s 256-token window: dense embeddings may truncate them, while keyword retrieval and generation text retain the full row. Do not change chunking or retrieval depth without testing source coverage and grounding.

Compose uses `redis://redis:6379/0` and `http://qdrant:6333` internally. Host defaults in `Settings` use localhost; host-side tests/evaluation must use explicit reachable addresses. Backend data and uploads use named volumes; see [Operations](operations.md) before changing them.

## Optional legacy parser

The legacy Unstructured parser is retained behind `uv sync --extra legacy-parser` (without the `runtime` group) and `PARSER_PROVIDER=unstructured`. It is not included or verified in the default minimal image. Re-ingest existing sources through the API to use the structure-preserving default parser.

## Azure environment

Use [`.env.azure.example`](../.env.azure.example) for the Azure adapter profile. Managed identity uses `AZURE_CLIENT_ID` and DefaultAzureCredential; a signed-in developer identity can be used for local administration with the required roles. Endpoint, deployment name, model/version identity, and vector dimensions must match deployed resources. Changing embedding dimensions requires a compatible index and re-ingestion. Do not mix local and Azure state/storage profiles. Runtime templates inject container-specific addresses and identity configuration.

## Supported provider selections

| Setting | Supported values | Requirements |
| --- | --- | --- |
| `PARSER_PROVIDER` | `structured`, `doc_intelligence`, `unstructured` | Structured is in the default image; Azure Document Intelligence needs its endpoint/key; Unstructured needs the optional extra. |
| `EMBEDDER_PROVIDER` | `sentence_transformers`, `azure_openai` | Local model weights, or an Azure embedding deployment and identity/key. |
| `VECTOR_STORE_PROVIDER` | `qdrant`, `azure_search` | Reachable Qdrant, or a compatible Azure Search index and shared Blob state. |
| `LLM_PROVIDER` | `nvidia_nim`, `ollama`, `azure_openai` | Your selected provider's model, endpoint and credentials. |
| `QUEUE_PROVIDER` | `rq`, `service_bus` | Redis/RQ worker, or Service Bus with the matching Azure worker command. |
| `SHARED_STATE_PROVIDER` | `redis`, `azure_blob` | Redis publication/job state, or Azure Blob state with managed identity. |
| `ORIGINALS_PROVIDER` | `local`, `azure_blob` | Shared API/worker upload directory, or private Blob originals. |

Keep storage, queue and state selections coherent. The supplied Azure templates configure the Azure set together; the local Compose profile starts an RQ worker. Merely replacing its environment file with the Azure example does not change the worker command or provide Azure identity access.

### Ollama generation with the local storage stack

Configure a separately running Ollama server and a model that supports the engine's structured-output requests:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.1:8b
```

Docker Desktop provides `host.docker.internal` for the host service. On Linux, configure an appropriate host-gateway mapping or another reachable service address. `localhost` inside a container refers to that container. Ollama is not included in the Compose file. Model compatibility must be tested with your documents; no provider switch guarantees equivalent answers. The current RAGAS judge builder supports Azure generation/judge roles or the default NVIDIA/local-embedding profile, not an Ollama judge.

### Azure role configuration

The key settings are `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_JUDGE_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX`, `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_SERVICE_BUS_NAMESPACE`, and `AZURE_SERVICE_BUS_QUEUE`. Model identity settings record model/version alongside deployment names. `AZURE_EMBEDDING_DIMENSIONS` must agree with the index.

Runtime managed identity uses `AZURE_CLIENT_ID`; DefaultAzureCredential supplies service tokens. Model and Search key settings are optional alternatives for compatible operator-managed setups. A signed-in host identity is not automatically available inside a container. Document Intelligence separately requires `AZURE_DOC_INTELLIGENCE_ENDPOINT` and `AZURE_DOC_INTELLIGENCE_API_KEY`.

### Application tokens and external environment files

Generate two distinct random tokens, for example by running `python -c 'import secrets; print(secrets.token_urlsafe(32))'` separately for each token. Store them privately in your environment file, and allow only the intended query scopes. `RAG_ENV_FILE=/absolute/path/to/your/environment.env docker compose -f infra/docker-compose.yml up --build -d` keeps populated values outside the checkout. API requests require matching bearer credentials; interactive Swagger UI still requires authorization for protected routes.
