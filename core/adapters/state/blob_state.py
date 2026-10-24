"""Private JSON state and renewable distributed locks backed by Blob Storage."""

import json
import threading
import time
from contextlib import contextmanager

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from core.config import get_settings


class BlobState:
    def __init__(self, service=None):
        settings = get_settings()
        self._credential = None
        if service is None:
            if not settings.azure_storage_account_url:
                raise ValueError("Selected storage adapter requires configuration")
            self._credential = DefaultAzureCredential()
            service = BlobServiceClient(settings.azure_storage_account_url, credential=self._credential)
        self._leases = {}
        self.service = service
        self.container = service.get_container_client(settings.azure_state_container)

    def close(self):
        try:
            self.service.close()
        finally:
            if self._credential is not None:
                self._credential.close()

    def read(self, name: str, default=None):
        try:
            return json.loads(self.container.get_blob_client(name).download_blob().readall())
        except ResourceNotFoundError:
            return default

    def write(self, name: str, value) -> None:
        self.container.get_blob_client(name).upload_blob(
            json.dumps(value).encode(), overwrite=True, lease=self._leases.get(name)
        )

    def delete(self, name: str) -> None:
        try:
            self.container.delete_blob(name)
        except ResourceNotFoundError:
            pass

    @contextmanager
    def lock(self, name: str, wait: float = 30):
        blob = self.container.get_blob_client(name)
        try:
            blob.upload_blob(b"{}", overwrite=False)
        except ResourceExistsError:
            pass
        deadline = time.monotonic() + wait
        while True:
            try:
                lease = blob.acquire_lease(lease_duration=60)
                break
            except HttpResponseError as error:
                if error.status_code != 409 or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        self._leases[name] = lease
        stopped = threading.Event()
        lost = threading.Event()

        def renew():
            while not stopped.wait(15):
                try:
                    lease.renew()
                except Exception:  # noqa: BLE001 -- lease failure must fail closed
                    lost.set()
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield lost
            if lost.is_set():
                raise RuntimeError("Distributed lease was lost")
        finally:
            stopped.set()
            thread.join(timeout=10)
            self._leases.pop(name, None)
            lease.release()


@contextmanager
def blob_state():
    state = BlobState()
    try:
        yield state
    finally:
        state.close()
