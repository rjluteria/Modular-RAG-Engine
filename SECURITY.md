# Security

## Report a vulnerability

Do not put credentials, private documents, exploit payloads containing personal data, or sensitive endpoint details in a public issue. If the repository's **Security → Report a vulnerability** option is available, use it for a private report. Otherwise contact the maintainer through a verified private channel before sharing sensitive details. Private reporting availability depends on the repository settings; this project does not assume it is enabled.

Provide affected versions, a minimal synthetic reproduction, expected/observed behavior, and impact. Avoid testing systems you do not own. No response-time guarantee is currently offered.

## Authentication and authorization

All document/query operations require a bearer service credential. Admin and user tokens must differ. Empty credentials grant no access. The user token queries only explicitly allowed `use_case` scopes; an empty allowlist denies user queries. Admin operations remain privileged. These shared service tokens are not an individual identity or tenant-management system. Integrate your own trusted identity boundary before serving multiple end users.

Keep tokens on a trusted application backend, not in a browser bundle. Restrict CORS origins explicitly; CORS does not replace authorization. Use TLS for remote access. See [API Reference](docs/api.md) and [Configuration](docs/configuration.md).

## Input, evidence, and data isolation

Uploads have size, signature, archive, page and table-expansion bounds. Server-generated storage names avoid trusting client filenames. Workers sanitize failures instead of exposing upstream payloads. Scoped retrieval excludes unpublished or foreign source revisions. Structured evidence selections are verified against the supplied context before rendering an answer, but these checks cannot prove semantic correctness.

Publish complete source revisions only after storage succeeds. Redis manifests and Qdrant data must be preserved/restored together. Azure state uses renewable leases and lease-qualified writes to prevent stale owners from publishing. Original expiry and result retention are application data-lifecycle features; a context already copied for an in-flight answer cannot be recalled by later deletion.

## Deployment security

The runtime image runs as nonroot with no shell or OS package manager. Compose drops capabilities, disallows privilege escalation, uses a read-only root, and bounds memory/process counts. Writable document/model volumes remain required. Default backend services have no host ports; API binding is loopback-only. Existing root-owned volumes may need a content-preserving ownership migration.

Azure uses scoped managed-identity access to Blob, Search, Service Bus, the model account and registry. Application bearer tokens are separate from Azure service identity. Public HTTPS service endpoints are supplied; private networking is not included. Review RBAC, exposure and permitted outbound hosts for your deployment. Readiness does not run generation and does not perform a complete Azure Service Bus exchange.

Hosted generation/judging sends selected document context to the configured provider. Choose providers and retention practices appropriate to your documents. Do not enable arbitrary document-controlled URL/file resource loading. Do not log credentials, document bodies, provider responses or sensitive URLs. The supplied API image and service commands disable access logging.

## Dependency and image verification

Install the frozen dependency set and run the [developer checks](docs/evaluation.md). The runtime excludes evaluation/legacy parser dependencies. Preserve native-package identity and third-party legal notices when building minimal images so scanners retain visibility.

The image gate reports every severity and rejects high/critical findings, detected secrets, missing evidence, and available unapplied fixes except low-severity entries. Medium/low findings may remain and require reachability/impact review; a passing gate is not a claim of invulnerability. Scan all exact delivery images, rebuild security-sensitive cached layers when updates become available, and retain detailed reports outside public source. Dependency lookup audits cannot assess editable project code or a local maintained wheel.

Setuptools is constrained to >=84.1.0,<85 for the issue described in [GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w). Recheck advisories before release rather than relying on a historical scan.

## Maintained text-only evaluation dependency

The project uses a reproducible RAGAS 0.4.3.post2 wheel derived from upstream 0.4.3. It disables URL/local-file image loading and the unused pickle cache, removes DiskCache, and repairs an unavailable optional Vertex import. Required text metric implementations remain unchanged. Unsupported features and licenses are documented in [vendor/ragas](vendor/ragas/README.md).

The patch addresses exposure associated with the upstream [RAGAS resource-loading advisory](https://github.com/advisories/GHSA-95ww-475f-pr4f) and [DiskCache pickle advisory](https://github.com/advisories/GHSA-w8v5-vhqr-4h9v); it is not an upstream patched release. Rebuild from the pinned upstream wheel, compare its hash and installed bytes, test that hostile contexts cannot read files or make network requests, verify DiskCache is unavailable, and retain all four text metrics. These checks supplement the normal package audit; do not suppress findings to pass CI.

## Remaining boundaries

Optional providers, OCR, complex tables, large-corpus scalability, concurrent load and production network design require deployment-specific acceptance checks. Prompt injection and incorrect evidence selection remain risks even with provenance validation. Report and investigate failures using synthetic reproductions. Never point destructive service tests at application data. Preserve third-party notices; no project-wide license is granted by this document.
