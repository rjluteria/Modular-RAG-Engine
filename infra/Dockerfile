FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
COPY infra/uv-requirements.txt /tmp/uv-requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/uv-requirements.txt
WORKDIR /app
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
ENV DO_NOT_TRACK=1 HF_HUB_DISABLE_TELEMETRY=1 RAGAS_DO_NOT_TRACK=true UNSTRUCTURED_DISABLE_NOTICE=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv sync --frozen --no-install-project --no-dev --group runtime --no-cache
COPY infra/runtime_libraries.py /tmp/runtime_libraries.py
RUN python /tmp/runtime_libraries.py

FROM gcr.io/distroless/cc-debian13:nonroot@sha256:3ee00291cf386ec52a552e6a858d555debd74776eb4bfa53fa3df2cd6677515e
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY --from=build /opt/venv /opt/venv
COPY --from=build /runtime-libs/ /
COPY core ./core
COPY api ./api
COPY worker ./worker
COPY pyproject.toml uv.lock ./
COPY --from=build --chown=65532:65532 /runtime-dirs/ /
ENV PYTHONPATH=/app
ENV PATH="/opt/venv/bin:/usr/local/bin:$PATH" HOME=/home/nonroot HF_HOME=/home/nonroot/.cache/huggingface
ENV DO_NOT_TRACK=1 HF_HUB_DISABLE_TELEMETRY=1 RAGAS_DO_NOT_TRACK=true
USER 65532:65532
EXPOSE 8000
ENTRYPOINT []
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
