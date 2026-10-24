"""Use one ephemeral registry credential file without machine credential helpers."""

import base64
import json
import os
import re
import tempfile
from pathlib import Path


def push_image(run, registry: str, server: str, image: str, private: Path) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}\.azurecr\.io", server) or not image.startswith(server + "/"):
        raise ValueError("Registry/image target is invalid")
    endpoint = run("docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}", capture=True).strip()
    if not endpoint.startswith("unix:///"):
        raise ValueError("A local Docker Unix socket is required")
    token = json.loads(run("az", "acr", "login", "-n", registry, "--expose-token", "-o", "json", capture=True))
    if token.get("loginServer") != server or not token.get("accessToken"):
        raise ValueError("Registry token target mismatch")
    with tempfile.TemporaryDirectory(prefix="acr-auth-", dir=private) as directory:
        path = Path(directory) / "config.json"
        auth = base64.b64encode(("00000000-0000-0000-0000-000000000000:" + token["accessToken"]).encode()).decode()
        # Nonempty auths prevents Docker's automatic default helper discovery.
        path.write_text(json.dumps({"auths": {server: {"auth": auth}}}))
        path.chmod(0o600)
        environment = os.environ.copy()
        for name in ("DOCKER_AUTH_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            environment.pop(name, None)
        run("docker", "--config", directory, "--host", endpoint, "push", image, env=environment)
