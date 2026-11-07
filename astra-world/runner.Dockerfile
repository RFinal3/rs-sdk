# syntax=docker/dockerfile:1
# The runner: always built from Ryan's checkout (never from Astra's source), because it is
# where STOP and limits are enforced. Python + the kit + runner.py, nothing else.
#
# Built from the repo root:  docker compose -f astra-world/compose.yaml build runner

# Same base as the player image (same `bun` user, uid 1000, for writes into Astra's folder).
ARG BUN_IMAGE=oven/bun:debian@sha256:93d5032fe06d163427822dc1598f420363759bb18155c9cc90eb1be05a64a19e
FROM ${BUN_IMAGE}

RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/rs
COPY astra-world/kit astra-world/kit
COPY astra-world/runner/runner.py astra-world/runner/runner.py

ENV PYTHONPATH=/opt/rs/astra-world/kit:/opt/rs/astra-world/runner

RUN mkdir -p /harness /owner /tmp/runner && chown bun:bun /tmp/runner
USER bun

ENTRYPOINT ["python3", "/opt/rs/astra-world/runner/runner.py"]
