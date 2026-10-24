FROM qdrant/qdrant:v1.19.2@sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad AS upstream
USER 0
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /runtime-libs/var/lib/dpkg/status.d /runtime-dirs/qdrant/storage /runtime-dirs/qdrant/snapshots \
    && ldd /qdrant/qdrant | awk '/=> \/.*lib(unwind|lzma)/ {print $3}' > /tmp/libraries \
    && while read -r library; do \
         resolved="$(readlink -f "$library")"; \
         directory="/runtime-libs$(dirname "$resolved")"; \
         mkdir -p "$directory"; cp -L "$library" "$directory/$(basename "$library")"; \
         dpkg-query -S "$resolved" | sed 's/: \/.*//' >> /tmp/packages; \
       done < /tmp/libraries \
    && sort -u /tmp/packages | while read -r package; do \
         dpkg-query -s "$package" >> /runtime-libs/var/lib/dpkg/status.d/qdrant-runtime-libraries; \
         printf '\n' >> /runtime-libs/var/lib/dpkg/status.d/qdrant-runtime-libraries; \
         name="${package%%:*}"; \
         if test -f "/usr/share/doc/$name/copyright"; then \
           mkdir -p "/runtime-libs/usr/share/doc/$name"; \
           cp "/usr/share/doc/$name/copyright" "/runtime-libs/usr/share/doc/$name/copyright"; \
         fi; \
       done
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:3ee00291cf386ec52a552e6a858d555debd74776eb4bfa53fa3df2cd6677515e
COPY --from=upstream /qdrant/qdrant /qdrant/qdrant
COPY --from=upstream /qdrant/qdrant.spdx.json /qdrant/qdrant.spdx.json
COPY --from=upstream /qdrant/config/ /qdrant/config/
COPY --from=upstream /runtime-libs/ /
COPY --from=upstream --chown=65532:65532 /runtime-dirs/ /
COPY infra/licenses/QDRANT-LICENSE /qdrant/LICENSE
WORKDIR /qdrant
ENV RUN_MODE=production QDRANT__TELEMETRY_DISABLED=true
USER 65532:65532
ENTRYPOINT ["/qdrant/qdrant"]
