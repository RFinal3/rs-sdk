#!/bin/bash
# Player container entrypoint: headless client (or wait for a browser client), then the bridge.
#
# Env:
#   BOT_USERNAME   the character (default Astra)
#   PLAYER_CLIENT  lite (default): headless client runs here
#                  headless-browser: the camera mode; Astra's client is a headless Chromium
#                           page here (bridge/camera.ts), so GET /screenshot has a real picture
#                  browser: Ryan attaches the character in his browser (watch mode A);
#                           the bridge waits for that client
#   WORLD_HOST     the world container's name on the game network (default world)
#
# The SDK and the lite client use plain http/ws only for "localhost"/"127.*" hosts,
# so a loopback forwarder (bridge/forward.ts) carries 8888 and 7780 to the world.
set -euo pipefail

BOT_USERNAME="${BOT_USERNAME:-Astra}"
PLAYER_CLIENT="${PLAYER_CLIENT:-lite}"
ENV_DIR="/opt/rs/bots/${BOT_USERNAME}"
SECRET="/secrets/${BOT_USERNAME}.env"

# The character's game password: generated once, kept in the player-secrets volume.
# It only guards an account on Ryan's private, loopback-only world.
if [ ! -s "$SECRET" ]; then
    umask 077
    PASS="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
    printf 'BOT_USERNAME=%s\nPASSWORD=%s\n' "$BOT_USERNAME" "$PASS" > "$SECRET"
    echo "[player] created credentials for ${BOT_USERNAME} in ${SECRET}"
fi

mkdir -p "$ENV_DIR"
{
    cat "$SECRET"
    echo "SERVER=localhost:8888"
    echo "GATEWAY_URL=ws://localhost:7780"
    echo "SHOW_CHAT=${SHOW_CHAT:-false}"
    echo "TELEMETRY=false"
} > "$ENV_DIR/bot.env"
chmod 600 "$ENV_DIR/bot.env"

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

WORLD_HOST="${WORLD_HOST:-world}"
bun /opt/rs/astra-world/bridge/forward.ts "8888=${WORLD_HOST}:8888" "7780=${WORLD_HOST}:7780" &

# Wait for the world's web port (it can still be starting after a restart).
echo "[player] waiting for the world..."
python3 - <<'PY'
import time, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.time() + 300
while True:
    try:
        if opener.open("http://127.0.0.1:8888/rs2.cgi", timeout=5).status == 200:
            print("[player] world is up", flush=True)
            break
    except Exception:
        pass
    if time.time() > deadline:
        raise SystemExit("[player] world did not come up within 300s")
    time.sleep(2)
PY

CLIENT_PID=0
if [ "$PLAYER_CLIENT" = "lite" ]; then
    echo "[player] starting headless client for ${BOT_USERNAME}"
    ( cd /opt/rs/server/webclient && set -a && . "$ENV_DIR/bot.env" && set +a && exec bun src/lite/runner.ts "$BOT_USERNAME" ) &
    CLIENT_PID=$!
elif [ "$PLAYER_CLIENT" = "headless-browser" ]; then
    if [ ! -x "${CHROMIUM_PATH:-/usr/bin/chromium}" ]; then
        echo "[player] PLAYER_CLIENT=headless-browser needs Chromium in the image; rebuild with PLAYER_CAMERA=true" >&2
        exit 1
    fi
    echo "[player] starting the camera client (headless Chromium) for ${BOT_USERNAME}"
    ( cd /opt/rs && exec bun astra-world/bridge/camera.ts "$BOT_USERNAME" ) &
    CLIENT_PID=$!
else
    echo "[player] PLAYER_CLIENT=browser: open the bot client for ${BOT_USERNAME} in your browser"
    echo "[player]   credentials: docker compose -f astra-world/compose.yaml exec player cat ${SECRET}"
fi

# Wait until the gateway reports the character in game before connecting the bridge.
# If the headless client dies first, exit so compose restarts the container cleanly.
echo "[player] waiting for ${BOT_USERNAME} to be in game..."
python3 - "$BOT_USERNAME" "$CLIENT_PID" <<'PY'
import json, os, sys, time, urllib.request
name, pid = sys.argv[1], int(sys.argv[2])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
last = 0.0
while True:
    if pid:
        try:
            with open(f"/proc/{pid}/stat") as f:
                dead = f.read().rsplit(")", 1)[1].split()[0] in ("Z", "X")  # zombie: exited, not yet reaped
        except FileNotFoundError:
            dead = True
        if dead:
            raise SystemExit("[player] headless client exited before getting in game")
    try:
        bots = json.load(opener.open("http://127.0.0.1:7780/", timeout=5)).get("bots", {})
        if bots.get(name, {}).get("inGame"):
            print(f"[player] {name} is in game", flush=True)
            break
    except Exception:
        pass
    if time.time() - last > 30:
        print(f"[player] still waiting for {name}...", flush=True)
        last = time.time()
    time.sleep(1)
PY

cd /opt/rs
# If the client drops later, the bridge aborts (onDisconnect: 'error'), the container
# exits and compose restarts it (restart: unless-stopped).
#
# Astra can restart the player (to pick up her edited bridge/kit/player-entry.sh, which are
# mounted live) by creating <harness>/control/restart-player. The file is removed and the
# container exits; compose starts it again, and the runner follows (card_interrupted, replay).
RESTART_REQUEST=/harness/control/restart-player
bun astra-world/bridge/bridge.ts "$BOT_USERNAME" &
BRIDGE_PID=$!
while kill -0 "$BRIDGE_PID" 2>/dev/null; do
    if [ -e "$RESTART_REQUEST" ]; then
        echo "[player] restart requested ($RESTART_REQUEST); exiting so compose restarts the player"
        rm -f "$RESTART_REQUEST"
        exit 0
    fi
    sleep 3
done
wait "$BRIDGE_PID"
