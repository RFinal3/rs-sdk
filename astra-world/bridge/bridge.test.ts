/** HTTP regressions with the real bridge/execution code and a simulated SDK. */
import { afterAll, beforeAll, expect, mock, spyOn, test } from 'bun:test';
import { executionContext, executionWait } from '../../sdk/execution-context';

let startBridge: (context: any) => Promise<void>;
const previousPort = process.env.BRIDGE_PORT;
const previousToken = process.env.BRIDGE_TOKEN;

beforeAll(async () => {
    process.env.BRIDGE_PORT = '0';
    process.env.BRIDGE_TOKEN = '';
    mock.module('../../sdk/runner', () => ({
        runScript: async (callback: typeof startBridge) => { startBridge = callback; },
    }));
    await import('./bridge');
});
afterAll(() => {
    if (previousPort === undefined) delete process.env.BRIDGE_PORT;
    else process.env.BRIDGE_PORT = previousPort;
    if (previousToken === undefined) delete process.env.BRIDGE_TOKEN;
    else process.env.BRIDGE_TOKEN = previousToken;
    mock.restore();
});

function fixture(failStop = false) {
    let server: ReturnType<typeof Bun.serve>;
    let state: any = { tick: 10, revision: 10, inGame: true,
        player: { worldX: 50, worldZ: 60, level: 0, lifeId: 1 } };
    let publish: (() => void) | undefined;
    let actionStarted!: () => void;
    const started = new Promise<void>(resolve => { actionStarted = resolve; });
    let stopStarted!: () => void;
    const stopping = new Promise<void>(resolve => { stopStarted = resolve; });
    const walks: unknown[][] = [];
    const sdk = {
        getState: () => state,
        isConnected: () => true,
        sendWalk: async (...args: unknown[]) => {
            walks.push(args);
            if (failStop) stopStarted();
            return { success: !failStop, message: failStop ? 'disconnected' : 'sent' };
        },
        waitForCondition: (predicate: (s: any) => boolean) => executionWait<any>(resolve => {
            publish = () => { if (predicate(state)) resolve(state); };
            stopStarted();
            return () => { publish = undefined; };
        }),
    };
    const bot = { walkTo: () => {
        actionStarted();
        return executionWait(() => () => {});
    } };
    const realServe = Bun.serve.bind(Bun);
    const serve = spyOn(Bun, 'serve').mockImplementation((options: any) => {
        server = realServe(options);
        return server;
    });
    const parent = new AbortController();
    const running = executionContext.run({ signal: parent.signal }, () => startBridge({ sdk, bot }));
    running.catch(() => {});
    serve.mockRestore();
    const post = (path: string, body: object) => fetch(`http://127.0.0.1:${server!.port}${path}`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
    const act = (id: string) => post('/act', { action_id: id, verb: 'walk_to',
        params: { x: 100, z: 100 }, deadline_ms: 10_000 });
    return { act, post, started, stopping, walks,
        tick: () => { state = { ...state, tick: state.tick + 1 }; publish?.(); },
        close: async () => { parent.abort(new Error('test finished')); await running.catch(() => {}); },
    };
}

test('/cancel waits for stop confirmation and /act remains busy until it finishes', async () => {
    const f = fixture();
    try {
        const action = f.act('moving');
        await f.started;
        let cancellationReturned = false;
        const cancel = f.post('/cancel', { action_id: 'moving' }).then(response => {
            cancellationReturned = true;
            return response;
        });
        await f.stopping;
        const busy = await f.act('during-stop');
        expect(busy.status).toBe(409);
        expect(await busy.json()).toMatchObject({ error: 'busy', in_flight: 'moving' });
        expect(cancellationReturned).toBe(false);
        f.tick();
        f.tick();
        const stillBusy = await f.act('after-two-ticks');
        expect(stillBusy.status).toBe(409);
        expect(cancellationReturned).toBe(false);
        f.tick();
        const cancelled = await cancel;
        expect(cancelled.status).toBe(200);
        expect(await cancelled.json()).toMatchObject({ cancelled: true, action_id: 'moving', stop: { confirmed: true } });
        expect(await (await action).json()).toMatchObject({ outcome: 'cancelled', stop: { confirmed: true } });
        expect(f.walks).toEqual([[50, 60, false]]);
        expect(await (await f.post('/cancel', {})).json()).toMatchObject({ reason: 'nothing_in_flight' });
    } finally { await f.close(); }
});

test('failed stop reports 503 cancellation and latches subsequent /act requests closed', async () => {
    const f = fixture(true);
    try {
        const action = f.act('moving');
        await f.started;
        const cancel = await f.post('/cancel', { action_id: 'moving' });
        expect(cancel.status).toBe(503);
        expect(await cancel.json()).toMatchObject({ cancelled: false, error: 'stop_unconfirmed', stop: { confirmed: false } });
        expect(await (await action).json()).toMatchObject({ outcome: 'cancelled', stop: { confirmed: false } });
        for (const id of ['retry-one', 'retry-two']) {
            const refused = await f.act(id);
            expect(refused.status).toBe(503);
            expect(await refused.json()).toMatchObject({ error: 'stop_unconfirmed' });
        }
        expect(f.walks).toHaveLength(1);
    } finally { await f.close(); }
});
