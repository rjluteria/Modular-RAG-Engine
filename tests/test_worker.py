from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest


def test_fatal_worker_failure_exits_unsuccessfully_without_private_payload(monkeypatch, caplog):
    from worker import servicebus_worker as worker

    @contextmanager
    def state():
        yield SimpleNamespace(lock=lambda *args, **kwargs: nullcontext())

    def fail():
        raise RuntimeError("private upstream payload")

    monkeypatch.setattr(worker, "blob_state", state)
    monkeypatch.setattr(worker, "_drain", fail)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 1
    assert "private upstream payload" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("during_work", [False, True])
def test_worker_conflict_only_skips_when_execution_lease_is_busy(monkeypatch, during_work):
    from azure.core.exceptions import HttpResponseError

    from worker import servicebus_worker as worker

    def conflict():
        error = HttpResponseError("Synthetic conflict")
        error.status_code = 409
        raise error

    @contextmanager
    def lock(*args, **kwargs):
        if not during_work:
            conflict()
        yield

    @contextmanager
    def state():
        yield SimpleNamespace(lock=lock)

    monkeypatch.setattr(worker, "blob_state", state)
    monkeypatch.setattr(worker, "_drain", conflict)
    if during_work:
        with pytest.raises(SystemExit) as error:
            worker.main()
        assert error.value.code == 1
    else:
        worker.main()
