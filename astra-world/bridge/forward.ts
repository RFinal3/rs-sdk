#!/usr/bin/env bun
/**
 * Loopback TCP forwarder for the player container.
 *
 * The SDK and the lite client only use plain http/ws for hosts that start with
 * "localhost" or "127." and assume TLS for anything else (e.g. "world:8888").
 * Rather than patch upstream, the player listens on 127.0.0.1 and forwards to the
 * world container, so bot.env can say SERVER=localhost:8888 as in a normal local setup.
 *
 *   bun astra-world/bridge/forward.ts 8888=world:8888 7780=world:7780
 */

/** Buffered writer: Bun's socket.write may accept only part of a chunk. */
class Out {
    private pending: Uint8Array[] = [];
    constructor(private sock: { write(b: Uint8Array): number; end(): void }) {}
    send(chunk: Uint8Array) {
        if (this.pending.length) { this.pending.push(chunk); return; }
        const n = this.sock.write(chunk);
        if (n < chunk.byteLength) this.pending.push(chunk.subarray(Math.max(n, 0)));
    }
    drain() {
        while (this.pending.length) {
            const head = this.pending[0]!;
            const n = this.sock.write(head);
            if (n < head.byteLength) { this.pending[0] = head.subarray(Math.max(n, 0)); return; }
            this.pending.shift();
        }
    }
}

type Side = { out: Out | null; peer: Out | null; early: Uint8Array[]; peerEnd: (() => void) | null; closed: boolean };

function pipe(localPort: number, host: string, port: number) {
    Bun.listen<Side>({
        hostname: '127.0.0.1',
        port: localPort,
        socket: {
            open(client) {
                const side: Side = { out: new Out(client), peer: null, early: [], peerEnd: null, closed: false };
                client.data = side;
                Bun.connect<{ out: Out }>({
                    hostname: host,
                    port,
                    socket: {
                        open(up) {
                            up.data = { out: new Out(up) };
                            if (side.closed) { up.end(); return; }
                            side.peer = up.data.out;
                            side.peerEnd = () => up.end();
                            for (const c of side.early) side.peer.send(c);
                            side.early = [];
                        },
                        data(_up, chunk) { side.out!.send(new Uint8Array(chunk)); },
                        drain(up) { up.data.out.drain(); },
                        close() { client.end(); },
                        error() { client.end(); },
                    },
                }).catch(() => client.end());
            },
            data(client, chunk) {
                const c = new Uint8Array(chunk);
                if (client.data.peer) client.data.peer.send(c);
                else client.data.early.push(c);
            },
            drain(client) { client.data.out?.drain(); },
            close(client) { client.data.closed = true; client.data.peerEnd?.(); },
            error(client) { client.data.closed = true; client.data.peerEnd?.(); },
        },
    });
    console.error(`[forward] 127.0.0.1:${localPort} -> ${host}:${port}`);
}

const specs = process.argv.slice(2);
if (!specs.length) {
    console.error('usage: forward.ts <localPort>=<host>:<port> ...');
    process.exit(2);
}
for (const spec of specs) {
    const m = /^(\d+)=([A-Za-z0-9.-]+):(\d+)$/.exec(spec);
    if (!m) { console.error(`bad spec: ${spec}`); process.exit(2); }
    pipe(Number(m[1]), m[2]!, Number(m[3]));
}
