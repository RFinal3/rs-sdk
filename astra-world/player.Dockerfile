# syntax=docker/dockerfile:1
# The player container: Astra's headless game client, the bridge (her hands), and
# Python for her harness. It joins only the internal `game` network, so nothing
# running here can reach the internet, the host, or the public demo server.
#
# Built from the repo root:  docker compose -f astra-world/compose.yaml build player

# Same base as upstream server/Dockerfile, pinned by digest (Bun 1.4.4, 2026-11-04).
# Override only for testing: --build-arg BUN_IMAGE=...
ARG BUN_IMAGE=oven/bun:debian@sha256:93d5032fe06d163427822dc1598f420363759bb18155c9cc90eb1be05a64a19e
FROM ${BUN_IMAGE}

RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/rs

# Dependencies first (cached unless lockfiles change).
COPY package.json bun.lock bunfig.toml tsconfig.json ./
RUN bun install --frozen-lockfile
COPY server/webclient/package.json server/webclient/bun.lock server/webclient/
RUN cd server/webclient && bun install --frozen-lockfile

# Sources the SDK, the lite client and the bridge need.
COPY sdk sdk
COPY server/vendor server/vendor
COPY server/webclient server/webclient
COPY astra-world/bridge astra-world/bridge
COPY astra-world/kit astra-world/kit
COPY astra-world/config astra-world/config
COPY astra-world/fixtures astra-world/fixtures
COPY --chmod=755 astra-world/player-entry.sh /usr/local/bin/player-entry.sh

ENV PYTHONPATH=/opt/rs/astra-world/kit \
    TELEMETRY=false \
    BRIDGE_PORT=7790

# Runs as the image's unprivileged `bun` user.
RUN mkdir -p /secrets /harness /owner /opt/rs/bots && chown bun:bun /secrets /harness /opt/rs/bots
USER bun

ENTRYPOINT ["/usr/local/bin/player-entry.sh"]
