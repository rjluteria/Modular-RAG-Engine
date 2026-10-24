"""Sanitized provider failure categories; never expose upstream response bodies."""


class ProviderConfigurationError(RuntimeError):
    """Selected provider configuration is incomplete."""


class GroundingError(RuntimeError):
    """Generated evidence failed local validation."""

    validation_response: dict | None = None


class ProviderRateLimitError(RuntimeError):
    status_code = 429


def public_model_error(error: Exception) -> dict[str, str]:
    if isinstance(error, GroundingError):
        return {
            "code": "answer_not_grounded",
            "message": "Generated evidence could not be verified; retry or refine the question",
        }
    if isinstance(error, ProviderConfigurationError):
        return {
            "code": "provider_configuration",
            "message": "Generation configuration requires administrator attention",
        }
    status = getattr(error, "status_code", None)
    if status == 429:
        return {"code": "provider_rate_limited", "message": "Generation capacity is busy; retry later"}
    if status in (400, 422):
        return {
            "code": "provider_configuration",
            "message": "Generation configuration requires administrator attention",
        }
    if status in (401, 403):
        return {
            "code": "provider_credentials",
            "message": "Generation credentials require administrator attention",
        }
    if status in (404, 410):
        return {"code": "model_unavailable", "message": "Configured generation model is unavailable"}
    return {"code": "provider_unavailable", "message": "Generation unavailable; retry later"}
