#!/usr/bin/env python3
"""Run inside the player container: confirms it can reach the world and nothing else.

    docker compose -f astra-world/compose.yaml exec player python3 /opt/rs/astra-world/kit/isolation_check.py
"""
import socket
import sys

ok = True


def reach(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=4):
            return True
    except OSError:
        return False


def check(name: str, cond: bool) -> None:
    global ok
    ok &= cond
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


check("world game port reachable (world:8888)", reach("world", 8888))
check("world gateway reachable (world:7780)", reach("world", 7780))
check("public demo server NOT reachable (rs-sdk-demo.fly.dev:443)", not reach("rs-sdk-demo.fly.dev", 443))
check("internet NOT reachable (1.1.1.1:443)", not reach("1.1.1.1", 443))
check("host gateway NOT reachable (host.docker.internal:7780)", not reach("host.docker.internal", 7780))
sys.exit(0 if ok else 1)
