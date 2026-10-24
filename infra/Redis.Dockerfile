FROM redis:7.4.11-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499
USER 0
RUN apk upgrade --no-cache
USER redis
