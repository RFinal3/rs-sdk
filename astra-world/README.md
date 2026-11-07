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

## Astra plays by herself (the runner)

The `runner` service starts with the stack and keeps Astra playing while the world is up. Design: vault `agents/rs-sdk-jev/docs/runner/RUNNER-DESIGN-2026-11-07.md`.

1. Point it at Astra's folder: `ASTRA_HARNESS_DIR=C:/path/to/astra_projects/rs-sdk-play/harness` in `astra-world/.env`.
2. `docker compose -f astra-world/compose.yaml up -d --build`.
3. Message Astra. She queues cards (`cards/queue/*.json`, her goal files) and reads what happened in `inbox/`. The runner plays one card at a time, as a fresh run of her `astra_loop.py`.

What the runner writes into her folder: `status.json` (what it's doing now), `inbox/` (one JSON file per event), `world/` (the game as the bridge sees it, every 5 s: `observe.json`, `summary.txt`, `health.json`, `verbs.json`), `world/screenshots/` (on request), and it moves cards between `cards/queue|active|done|failed`. It never acts in the game itself and never sees the character's password.

- **Stop:** create `astra-world/owner/STOP` (an empty file, no extension). No new card starts. A card in play is stopped within ~15 s (SIGTERM, then one `/cancel`, then kill), and plays again once STOP is removed. Saves are kept.
- **"needs_ryan":** if the bridge can't confirm a stop, the runner starts nothing more. Look at the game, then `docker compose -f astra-world/compose.yaml restart player`.
- **Watch it:** `docker compose -f astra-world/compose.yaml logs -f runner`, or open `status.json` in her folder.
- **Don't** run `astra_loop.py` by hand with `exec player` while the runner is up: two sessions would fight over the character. `live_check.py` is fine between cards.

### Screenshots (camera mode)

With `PLAYER_CLIENT=headless-browser` in `astra-world/.env` (then `docker compose -f astra-world/compose.yaml up -d`), Astra's client runs as a headless Chromium page inside the player container instead of the lite client. She asks for a picture by writing `requests/screenshot-<id>.json`; the runner saves `world/screenshots/<id>.png` and tells her in `inbox/`. With the default `lite` client, requests come back `screenshot_failed` (lite can't draw). The image includes Chromium unless built with `PLAYER_CAMERA=false` in `.env`.

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

## Review follow-up (2026-11-05)

The earlier live check only verified that cancellation stopped short of the final target; it did not prove immediate stopping. The bridge now dispatches a replacement route and checks three consecutive fresh ticks at the stop tile. `kit/live_check.py` also checks four later ticks without issuing an intervening action. See HANDOFF.md for the bounded confirmation and server-acknowledgement limitations.

Offline validation of the follow-up uses Bun 1.4.4: bridge cancellation/deadline tests, real lite-client packet encoding, SDK cancellation/walk regressions, and 34 Python kit tests. Root and bridge TypeScript checks pass. Container, live-world, Windows, and browser-attached checks have not been rerun for these fixes. CI now explicitly runs the bridge and Python kit suites.

## Runner and camera: what's tested (2026-11-07, Claude, Linux cloud container)

Images built from this branch (with the sandbox's proxy CA added in a local-only copy of the Dockerfiles; the committed Dockerfiles are unchanged apart from the runner and camera lines). Astra's harness was her GitHub version, `rs-sdk-play` `d986ad6` (her newer `a801a29` is only on Ryan's PC).

| Check | Result |
|---|---|
| `live_check.py` on the new player image | 12/12 |
| Runner plays a card with her real harness to `goal_reached: Woodcutting 2 >= 2`; card filed in `done/`, events in `inbox/` | pass, about 50 s |
| Fresh character still in the tutorial: both cards end `no_options` → `card_needs_astra` | pass (her v1 harness doesn't skip the tutorial) |
| STOP mid-card: session stopped in ~2 s, card stays in `active/`, the in-flight action cancelled (bridge busy → idle) | pass, after a fix found in this test |
| STOP removed: the same card plays again | pass |
| `docker compose down` / `up` mid-card: card stays in `active/`, `card_interrupted` at restart, played again first | pass |
| Mirror: `observe.json`, `summary.txt`, `health.json`, `verbs.json` refresh every 5 s | pass |
| Camera mode (`PLAYER_CLIENT=headless-browser`): Chromium client logs in; a screenshot request gives a 765×503 PNG of her view | pass |
| Character password absent from every file in her folder; runner has no secrets volume | pass |
| Camera cost | player ~730 MB RAM, ~45% of one core at 5 fps; image 1.75 GB |

Offline: 27 runner tests, 34 kit tests, 25 bridge tests (3 new for `/screenshot`), root type check. **Not tested:** Windows / Docker Desktop (including `network_mode: service:player` and writes into Ryan's folder from the container's `bun` user), Astra's `a801a29` harness, Jev. A lite-client screenshot request fails, as it should; with the bridge's 10 s wait it reports a timeout rather than `no_picture`.

## Files

| Path | What |
|---|---|
| `compose.yaml`, `player.Dockerfile`, `player-entry.sh` | the stack |
| `bridge/bridge.ts`, `bridge/verbs.ts` | the bridge and its fixed verb table (28 verbs) |
| `bridge/forward.ts` | loopback forwarder (the SDK only speaks plain http/ws to `localhost`) |
| `bridge/camera.ts` | camera mode: Astra's client in headless Chromium, so `GET /screenshot` has a picture |
| `runner/runner.py` | the runner (cards, STOP, mirror, inbox, screenshots); tests in `runner/runner_tests/` |
| `kit/rsworld/` | Python: `bridge_client`, `contracts` (limits/goal/progress), `decider` (mock deciders, answer validation) |
| `config/` | example limits and goal |
| `fixtures/` | real states and act responses captured 2026-11-04, for offline tests |
| `harness/` | default harness folder; `example_loop.py` smoke test |
| `owner/` | your folder (read-only to the player) |
| `../server/Dockerfile.dockerignore` | keeps compose builds from sending `node_modules` (same rules as `server/.dockerignore`) |
