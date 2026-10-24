# Azure deployment architecture

The runtime uses existing Microsoft Foundry model deployments and Azure AI Search. It adds Blob Storage (private originals and state), Service Bus, Container Registry, a user-assigned managed identity, a Container Apps environment/API, and a manual Container Apps worker job. The API uses the same `/api/v1` contracts as Docker. No frontend is deployed.

## Template entry points

| Template | Purpose |
| --- | --- |
| `infra/bicep/search.bicep` | Search service |
| `infra/bicep/foundry.bicep` | Native generation/judge/embedding deployments and Search project connection in an existing account/project |
| `infra/bicep/registry.bicep` | Container registry |
| `infra/bicep/main.bicep` | Runtime, storage, queue, managed identity, scoped roles, API and manual worker; references existing models/Search/registry |
| `infra/bicep/blob-connection.bicep` | Supported Blob connection in an existing Foundry project |
| `infra/bicep/empty-index.json` | Explicit 1,536-dimensional Search index schema |

Compile templates locally before use:

```sh
az bicep build --file infra/bicep/main.bicep --stdout > /tmp/runtime-template.json
```

Review every required parameter in the compiled schema. Use an external parameter file with your own resource groups, endpoint, existing deployment names, model/version identities, registry, immutable image reference, authorized principal ID/type, distinct application tokens, origins, and approved scopes. Never check populated parameter files into source control.

## Deployment sequence

1. Select your subscription, region, resource groups, and authorized deployment identity. Confirm regional model availability, quota, naming, Search capacity, and role-assignment permissions.
2. Reuse or provision the selected Search service and native model deployments. Inspect `foundry.bicep` before use: it creates models, so do not run it over existing deployments unintentionally. The runtime template itself does not recreate the existing models.
3. Create the registry with `registry.bicep`. Registry build/push authorization is an operator prerequisite and is not assigned by this template. Build `infra/Dockerfile` for Linux amd64 and publish an immutable image reference into your registry using your own authorized workflow.
4. Deploy `main.bicep` using the reviewed external parameters. Role assignment propagation can delay initial access. Check the managed identity's Blob, Search, model, Service Bus, and registry roles at their actual scopes.
5. Create the Search index with the schema whose dimensions match the embedding deployment. For authenticated administration, run `uv run --group runtime python -m scripts.setup_search_index --endpoint https://YOUR-SEARCH.search.windows.net --dimensions 1536 --index-name rag-chunks`. This creates or updates index schema; it does not ingest documents. Use a new compatible index rather than changing dimensions underneath existing vectors.
6. Create the supported Blob project connection using the actual storage resource ID and endpoint. Verify Search and Blob connections inside Foundry. Connections are metadata/integration; runtime credentials and RBAC remain necessary.
7. Check API health, readiness, authentication failures, and job configuration before sending documents. Starting ingestion and generation is a separate operational action.

The configured worker is **manual**, not queue-triggered. After uploads enqueue work, an authorized operator starts the Container Apps job, for example `az containerapp job start --resource-group YOUR-GROUP --name YOUR-WORKER`. It drains the queue with bounded idle waiting. Inspect failed executions and poll the API job; do not infer success from a successful start command.

## Identity and integration

Managed identity accesses Blob, Search, Service Bus, the model account, and registry. The templates also grant the selected deployment/evaluation principal Blob access; inspect that assignment and use only the intended principal. App tokens are injected as Container Apps secrets and stay distinct from Azure service identity. Template APIs use public HTTPS endpoints; private networking is not provided.

The API, queue, and registry have no fabricated Foundry connections. The engine remains a REST backend; a chatbot connects through your trusted application backend. Model deployments live in the Foundry account, while supported connections belong to its project.

## Runtime settings and limitations

The supplied Foundry template selects GPT-4o `2024-11-20` for generation/judging and text-embedding-3-small `1` for embeddings. Verify current regional availability before deployment, and use compatible deployment names and dimensions. The Search template selects Free; capacity and feature limits must suit your application. The API template sets minimum replicas to 0 and maximum to 1; scale-from-zero can add latency. The worker job is manual. These are template settings, not performance or availability guarantees.

Read [Configuration](configuration.md), [Operations](operations.md), and [Security](../SECURITY.md) before adapting the templates. No live acceptance result is implied by local template compilation.
