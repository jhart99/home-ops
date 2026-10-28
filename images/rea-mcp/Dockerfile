# REA (Reverse Engineer Anything) MCP server for ToolHive.
#
# ToolHive runs this container as UID/GID 1000 with readOnlyRootFilesystem: true,
# capabilities dropped, seccomp RuntimeDefault. Every runtime write therefore has
# to land on a mounted volume (declared in the MCPServer podTemplateSpec):
#   /home/node -> emptyDir  REA state/evidence + Ghidra user settings
#   /tmp       -> emptyDir  JVM and Ghidra headless scratch
#   /targets   -> PVC       binaries under analysis
#
# Verified locally: `rea doctor --provider ghidra` reports healthy and
# open_binary/binary_overview drive Ghidra 12.1.4 headless against a real ELF
# under `docker run --read-only` with only those two tmpfs mounts.
#
# amd64 only: REA's Ghidra admission wants a native decompiler matching the host
# (linux_x86_64) and the public release archive ships no linux_arm_64 build, so
# the image is deliberately not built for the arm64 node.
FROM node:26-bookworm-slim@sha256:88e480776c6149ff1d098cd304f44bf18057fb5d284a3329b78162d29821e28c

ARG REA_VERSION=6.8.0
ARG GHIDRA_VERSION=12.1.4
# Ghidra publishes no .sha256 asset (404); this is GitHub's own release-asset
# "digest" field for ghidra_12.1.4_PUBLIC_20260921.zip.
ARG GHIDRA_URL=https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip
ARG GHIDRA_SHA256=ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db
ARG JDK_URL=https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz
ARG JDK_SHA256=d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c

# procps is mandatory: REA's process supervisor needs a procps-compatible `ps`
# on PATH for ownership checks before it launches Ghidra (or any other child).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl procps unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL --retry 3 -o /tmp/jdk.tgz "${JDK_URL}" \
    && echo "${JDK_SHA256}  /tmp/jdk.tgz" | sha256sum -c - \
    && mkdir -p /opt/jdk \
    && tar -xzf /tmp/jdk.tgz -C /opt/jdk --strip-components=1 \
    && rm /tmp/jdk.tgz \
    && chmod -R a+rX /opt/jdk

RUN curl -fsSL --retry 3 -o /tmp/ghidra.zip "${GHIDRA_URL}" \
    && echo "${GHIDRA_SHA256}  /tmp/ghidra.zip" | sha256sum -c - \
    && unzip -q /tmp/ghidra.zip -d /opt \
    && rm /tmp/ghidra.zip \
    && chmod -R a+rX "/opt/ghidra_${GHIDRA_VERSION}_PUBLIC"

RUN npm install -g "rea-agents@${REA_VERSION}" \
    && npm cache clean --force \
    && rea --version

ENV JAVA_HOME=/opt/jdk \
    GHIDRA_INSTALL_DIR=/opt/ghidra_${GHIDRA_VERSION}_PUBLIC \
    PATH=/opt/ghidra_${GHIDRA_VERSION}_PUBLIC/support:/opt/jdk/bin:${PATH} \
    HOME=/home/node

WORKDIR /targets

# ToolHive forces runAsUser 1000 (= the image's `node` user) regardless.
USER node

ENTRYPOINT ["/usr/local/bin/rea"]
CMD ["mcp"]

LABEL org.opencontainers.image.title="rea-mcp" \
      org.opencontainers.image.description="REA (Reverse Engineer Anything) MCP server with Ghidra provider" \
      org.opencontainers.image.source="https://github.com/jhart99/home-ops" \
      org.opencontainers.image.licenses="MIT"
