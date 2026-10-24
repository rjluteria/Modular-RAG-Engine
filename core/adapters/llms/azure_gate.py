"""Validate request size and coordinate Azure provider concurrency and pacing."""

import asyncio
import json
import time

from langchain_core.callbacks import AsyncCallbackHandler

from core.adapters.llms.errors import ProviderRateLimitError
from core.adapters.state.blob_state import blob_state
from core.config import get_settings


class AzureProviderGate(AsyncCallbackHandler):
    raise_error = True

    @staticmethod
    def reserve(run_id):
        settings = get_settings()
        with blob_state() as state, state.lock("provider-slot"):
            row = state.read("provider-slot", {})
            now = time.time()
            if now < row.get("not_before", 0):
                raise ProviderRateLimitError("Provider capacity is busy")
            # Retain ownership until completion or the full request timeout.
            row.update(owner=str(run_id), not_before=now + settings.model_timeout_seconds + 60)
            state.write("provider-slot", row)

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        size = sum(len(encoding.encode(str(message.content))) + 8 for batch in messages for message in batch)
        parameters = kwargs.get("invocation_params", {})
        schema = {k: parameters[k] for k in ("tools", "functions", "response_format") if k in parameters}
        size += len(encoding.encode(json.dumps(schema, default=str))) + 128
        if size > get_settings().max_model_input_tokens:
            raise ValueError("Configured model input limit exceeded")
        await asyncio.to_thread(self.reserve, run_id)

    @staticmethod
    def finish(run_id):
        with blob_state() as state, state.lock("provider-slot"):
            row = state.read("provider-slot", {})
            if row.get("owner") == str(run_id):
                row["not_before"] = time.time() + get_settings().provider_min_interval_seconds
                state.write("provider-slot", row)

    async def on_llm_end(self, response, *, run_id, **kwargs):
        await asyncio.shield(asyncio.to_thread(self.finish, run_id))

    async def on_llm_error(self, error, *, run_id, **kwargs):
        # Keep the conservative reservation on errors/timeouts/cancellation.
        pass
