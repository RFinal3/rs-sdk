# astra-world

Ryan's private RuneScape-era world (this repo's own Linux server image) plus Astra's player: her headless game client, the **bridge** (her hands), and a Python kit for the harness she writes around Jev. Everything runs in Docker as Linux containers. Nothing in the game loop needs Windows.

Design: vault `agents/evidence/rs-sdk-jev/2026-11-04-linux-only-spec-proposal-r3.md` (and r2 and r1 before it). Astra's brief: [HANDOFF.md](HANDOFF.md).

```
world   (server/Dockerfile: engine + gateway + web client; saves on a volume)
  │  game network (internal: no internet, no host)
player  (headless client → bridge on 127.0.0.1:7790 → Astra's harness, Python)
  │
your browser → http://127.0.0.1:8888/rs2.cgi   (ports bound to this PC only)
```

## Start it (Windows, Docker Desktop)

From the repo root, on branch `claude/great-fermi-pznhu4`:

```powershell
Copy-Item astra-world\config\limits.example.json astra-world\owner\limits.json
docker compose -f astra-world/compose.yaml up -d --build
```

The first build takes a few minutes. Then:

- **Play / watch:** open `http://127.0.0.1:8888/rs2.cgi`, choose *New User*, make your own character. Astra's character is `Astra`. She starts in the tutorial; `live_check.py` (below) or her harness skips it, and she appears in Lumbridge.
- **Check it works:** `docker compose -f astra-world/compose.yaml exec player python3 /opt/rs/astra-world/kit/live_check.py` (moves Astra a few tiles, chops one tree, tests refusals and cancel), and `... exec player python3 /opt/rs/astra-world/kit/isolation_check.py` (player can reach the world and nothing else).
- **Smoke-test the whole chain:** `... exec player python3 /harness/example_loop.py` (mock decider, no Jev, chops trees until the example goal or your limits stop it, writes a report to `astra-world/harness/reports/`).
- **Logs:** `docker compose -f astra-world/compose.yaml logs -f player`
- **Stop the world:** `docker compose -f astra-world/compose.yaml down` (saves stay in the volume).

## Your controls

| What | Where |
|---|---|
| Limits: session length, spend caps, enabled goal kinds, allowed areas, safety guards, quest reading | `astra-world/owner/limits.json` (mounted read-only into the player; gitignored) |
| **Stop now** | create a file `astra-world/owner/STOP` (a harness must check it before every action; the example loop does) |
| XP rate | `NODE_XPRATE` in `astra-world/.env` (default **1 = normal rates**; upstream rs-sdk ships 25×) |
| World speed | `NODE_TICKRATE` in `compose.yaml` (ms per tick, 400 = normal) |
| Chat visible to Astra | `SHOW_CHAT=true` in `astra-world/.env` (default false) |
| Where Astra's harness lives | `ASTRA_HARNESS_DIR=C:/path/to/her/folder` in `astra-world/.env` (default `astra-world/harness`) |

`astra-world/.env` is a plain `KEY=value` file you create; it's gitignored.

## Watch modes

- **Headless (default, `PLAYER_CLIENT=lite`)**: Astra's client runs in the container. You watch by logging in your own character and finding her.
- **Browser-attached (`PLAYER_CLIENT=browser`)**: Astra's game client is a tab in your browser, so you see exactly what she sees. Get her password with `docker compose -f astra-world/compose.yaml exec player cat /secrets/Astra.env`, then open `http://127.0.0.1:8888/bot?bot=Astra&password=<that password>`. Closing the tab disconnects her. *(The page is served; this mode has not been tested end to end yet.)*

## Backups

Stop the world, then:

```powershell
docker compose -f astra-world/compose.yaml stop world
docker run --rm -v rsworld_rsworld-data:/d -v ${PWD}/backups:/b debian tar czf /b/rsworld-backup.tgz -C /d .
docker compose -f astra-world/compose.yaml start world
```

## What's tested (2026-11-04, Claude, in a Linux cloud container)

| Check | How |
|---|---|
| World builds and runs on Linux, bare and as the `world` container | `bun` build + engine/gateway start; `server/Dockerfile` image + compose `healthy` |
| Astra's character attaches headless, skips the tutorial, walks, chops a tree; effects confirmed from fresh state | `kit/live_check.py`, 12/12, inside the player container |
| Refusals send nothing: unknown verb/param, target not in state, stale decision, busy; cancel stops a walk mid-route; deadlines | same |
| Player container reaches the world only: not the internet, the public demo server, or the host | `kit/isolation_check.py`, 5/5 |
| Saves survive restarting both containers; player recovers by itself when it starts before the world | `docker compose restart world player`, logs + inventory/XP read back |
| `NODE_XPRATE=1` gives normal XP (25 per normal log) | one chop, XP delta |
| The web client loads from the host | headless Chromium on `127.0.0.1:8888/rs2.cgi`, no page errors |
| Unit tests: 12 bridge (Bun), 29 kit (Python) | `bun test astra-world/bridge`, `cd astra-world/kit && python3 -m unittest discover -s tests -t .` |

**Not tested:** Docker Desktop on Windows specifically (the sandbox's network blocked Debian's package mirror, so the `apt` steps were swapped out for that run only; everything else in both Dockerfiles ran as committed); browser-attached mode end to end; anything Jev.

## Files

| Path | What |
|---|---|
| `compose.yaml`, `player.Dockerfile`, `player-entry.sh` | the stack |
| `bridge/bridge.ts`, `bridge/verbs.ts` | the bridge and its fixed verb table (28 verbs) |
| `bridge/forward.ts` | loopback forwarder (the SDK only speaks plain http/ws to `localhost`) |
| `kit/rsworld/` | Python: `bridge_client`, `contracts` (limits/goal/progress), `decider` (mock deciders, answer validation) |
| `config/` | example limits and goal |
| `fixtures/` | real states and act responses captured 2026-11-04, for offline tests |
| `harness/` | default harness folder; `example_loop.py` smoke test |
| `owner/` | your folder (read-only to the player) |
| `../server/Dockerfile.dockerignore` | keeps compose builds from sending `node_modules` (same rules as `server/.dockerignore`) |
