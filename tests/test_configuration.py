import pytest
from pydantic import ValidationError

from core.config import Settings


@pytest.mark.parametrize("values", [
    {"chunk_size": 0}, {"chunk_overlap": -1}, {"chunk_overlap": 800},
    {"retrieval_top_k": 0}, {"retrieval_top_k": 51},
    {"hybrid_dense_weight": 0, "hybrid_bm25_weight": 0},
    {"hybrid_dense_weight": -1}, {"max_upload_bytes": 0},
    {"model_timeout_seconds": 0}, {"provider_min_interval_seconds": -1},
])
def test_invalid_configuration_is_rejected_before_serving(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_verified_queue_and_retrieval_defaults():
    settings = Settings(_env_file=None)
    assert settings.azure_service_bus_queue == "rag-jobs"
    assert settings.retrieval_top_k == 8
    assert settings.max_model_input_tokens == 16000
