#!/usr/bin/env bun
/**
 * astra-world bridge: the harness's hands.
 *
 * One long-lived SDK connection for one bot, served as a tiny JSON API on
 * 127.0.0.1 only. Astra's Python harness calls it; it never sees TypeScript,
 * credentials, or the gateway.
 *
 *   GET  /health          connection + current tick/revision
 *   GET  /verbs           the fixed verb table (names, descriptions, param specs)
 *   GET  /observe         full current state + snapshot id (?summary=1 adds a text summary)
 *   POST /act             {action_id, verb, params, deadline_ms, decided_at_tick?, max_age_ticks?, expect_revision?}
 *   GET  /screenshot      PNG of the game client's canvas: {tick, revision, png_base64}; 422 no_picture
 *                         when the client can't draw (the lite client never can). Read-only.
 *   POST /cancel          {action_id?}   aborts the action in flight
 *
 * One action at a time (409 if busy). A refused action sends nothing to the game.
 *
 * Run:   bun astra-world/bridge/bridge.ts <botname>     (reads bots/<botname>/bot.env)
 * Env:   BRIDGE_PORT (default 7790), BRIDGE_TOKEN (optional; required as X-Bridge-Token if set)
 */
import { runScript } from '../../sdk/runner';
import { executionContext } from '../../sdk/execution-context';
import { formatWorldStateSummary } from '../../sdk/formatter';
import type { BotWorldState } from '../../sdk/types';
import { getVerb, BridgeError, bindParams, describeVerbs } from './verbs';

import { executeAction, type StopResult } from './action-execution';

const PORT = parseInt(process.env.BRIDGE_PORT || '7790', 10);
const TOKEN = process.env.BRIDGE_TOKEN || '';
const MAX_BODY = 64 * 1024;
const MAX_DEADLINE_MS = 300_000;
const ACTION_ID = /^[A-Za-z0-9._:-]{1,64}$/;
const SCREENSHOT_TIMEOUT_MS = 10_000;
const MIN_SCREENSHOT_B64 = 100;   // a real canvas PNG is far larger; `data:,` or a stub is not a picture

type InFlight = { actionId: string; verb: string; controller: AbortController; startedMs: number; done: Promise<Awaited<ReturnType<typeof executeAction>>> };

const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const stamp = (s: BotWorldState | null) => s ? { tick: s.tick, revision: (s as any).revision ?? null } : null;

function snapshotId(s: BotWorldState): string {
    return `r${(s as any).revision ?? 'x'}-t${s.tick}`;
}

async function readBody(req: Request): Promise<any> {
    const len = Number(req.headers.get('content-length') || '0');
    if (len > MAX_BODY) throw new BridgeError('too_large', 'request body too large');
    const text = await req.text();
    if (text.length > MAX_BODY) throw new BridgeError('too_large', 'request body too large');
    if (!text) return {};
    try { return JSON.parse(text); } catch { throw new BridgeError('bad_json', 'body is not valid JSON'); }
}

await runScript(async ({ bot, sdk }) => {
    let inFlight: InFlight | null = null;
    let stopFailure: StopResult | null = null;
    const parentSignal = executionContext.getStore()?.signal;

    const server = Bun.serve({
        hostname: '127.0.0.1',
        port: PORT,
        async fetch(req) {
            const url = new URL(req.url);
            if (TOKEN && req.headers.get('x-bridge-token') !== TOKEN) return json(401, { error: 'unauthorized' });
            try {
                if (req.method === 'GET' && url.pathname === '/health') {
                    const s = sdk.getState();
                    return json(200, { connected: sdk.isConnected(), in_game: !!s?.inGame, busy: inFlight?.actionId ?? null, stop_failure: stopFailure, ...stamp(s) });
                }
                if (req.method === 'GET' && url.pathname === '/verbs') {
                    return json(200, describeVerbs());
                }
                if (req.method === 'GET' && url.pathname === '/observe') {
                    const s = sdk.getState();
                    if (!s) return json(503, { error: 'no_state', message: 'game state not available yet' });
                    const body: any = { snapshot_id: snapshotId(s), captured_ms: Date.now(), ...stamp(s), busy: inFlight?.actionId ?? null, state: s };
                    if (url.searchParams.get('summary') === '1') body.summary = formatWorldStateSummary(s);
                    return json(200, body);
                }
                if (req.method === 'GET' && url.pathname === '/screenshot') {
                    return await screenshot();
                }
                if (req.method === 'POST' && url.pathname === '/cancel') {
                    const b = await readBody(req);
                    if (!inFlight) return json(200, { cancelled: false, reason: 'nothing_in_flight' });
                    if (b.action_id && b.action_id !== inFlight.actionId) {
                        return json(409, { cancelled: false, reason: 'different_action_in_flight', in_flight: inFlight.actionId });
                    }
                    const action = inFlight;
                    action.controller.abort(new Error('Cancelled by harness'));
                    const settled = await action.done;
                    const confirmed = settled.stop?.confirmed === true;
                    return json(confirmed ? 200 : 503, { cancelled: confirmed, action_id: action.actionId,
                        stop: settled.stop, error: confirmed ? null : 'stop_unconfirmed' });
                }
                if (req.method === 'POST' && url.pathname === '/act') {
                    return await act(await readBody(req));
                }
                return json(404, { error: 'not_found' });
            } catch (e) {
                if (e instanceof BridgeError) return json(400, { error: e.code, message: e.message });
                return json(500, { error: 'internal', message: String((e as Error)?.message ?? e) });
            }
        },
    });

    /** Read-only: ask the attached game client for a PNG of its canvas. Never acts in the game.
     *  The headless lite client answers with an empty data URL, which is a failure, never a picture. */
    async function screenshot(): Promise<Response> {
        let dataUrl: string;
        try {
            dataUrl = await sdk.sendScreenshot(SCREENSHOT_TIMEOUT_MS);
        } catch (e) {
            return json(503, { error: 'screenshot_failed', message: String((e as Error)?.message ?? e) });
        }
        const m = /^data:image\/png;base64,([A-Za-z0-9+/=]+)$/.exec(typeof dataUrl === 'string' ? dataUrl : '');
        if (!m || m[1]!.length < MIN_SCREENSHOT_B64) {
            return json(422, { error: 'no_picture', message: 'the game client returned no picture (the headless '
                + 'lite client cannot draw; run the player with PLAYER_CLIENT=headless-browser)' });
        }
        return json(200, { ...stamp(sdk.getState()), png_base64: m[1] });
    }

    async function act(b: any): Promise<Response> {
        const actionId = b?.action_id;
        if (typeof actionId !== 'string' || !ACTION_ID.test(actionId)) {
            throw new BridgeError('bad_params', 'action_id must be 1-64 chars of [A-Za-z0-9._:-]');
        }
        const verb = getVerb(b?.verb);
        if (!verb) throw new BridgeError('unknown_verb', `unknown verb '${b.verb}'`);
        const deadline = b.deadline_ms;
        if (!Number.isInteger(deadline) || deadline < 100 || deadline > MAX_DEADLINE_MS) {
            throw new BridgeError('bad_params', `deadline_ms must be an integer in [100, ${MAX_DEADLINE_MS}]`);
        }
        if (stopFailure) return json(503, { error: 'stop_unconfirmed', message: 'Stop was not confirmed; operator must inspect the game and restart the bridge', stop: stopFailure });
        if (inFlight) return json(409, { error: 'busy', in_flight: inFlight.actionId });

        const before = sdk.getState();
        if (!before) return json(503, { error: 'no_state' });
        // Freshness. `revision` advances on every tick even when nothing happens, so the
        // usual check is age in ticks: decided_at_tick (the snapshot's tick) + max_age_ticks.
        // expect_revision is a strict mode: refuse unless nothing at all was published since.
        if (b.decided_at_tick !== undefined) {
            const maxAge = b.max_age_ticks ?? 5;
            if (!Number.isInteger(b.decided_at_tick) || !Number.isInteger(maxAge) || maxAge < 0 || maxAge > 100) {
                throw new BridgeError('bad_params', 'decided_at_tick must be an integer; max_age_ticks an integer in [0, 100]');
            }
            const age = before.tick - b.decided_at_tick;
            if (age > maxAge) {
                return json(409, { error: 'stale', message: `decision is ${age} ticks old (max ${maxAge})`, age_ticks: age, ...stamp(before) });
            }
        }
        if (b.expect_revision !== undefined && b.expect_revision !== (before as any).revision) {
            return json(409, { error: 'stale', message: 'state published since the decision was made', expected: b.expect_revision, ...stamp(before) });
        }
        const params = bindParams(b.verb, verb, b.params, before); // throws -> 400, nothing sent

        const controller = new AbortController();
        const onParentAbort = () => controller.abort(parentSignal?.reason);
        parentSignal?.addEventListener('abort', onParentAbort, { once: true });
        const startedMs = Date.now();
        if (parentSignal?.aborted) onParentAbort();
        // Defer execution until the in-flight slot exists, including synchronous verbs.
        const done = Promise.resolve().then(() => executeAction(sdk, controller, deadline,
            () => verb.run({ bot, sdk, state: before }, params)));
        inFlight = { actionId, verb: b.verb, controller, startedMs, done };
        const settled = await done;
        if (settled.stop && !settled.stop.confirmed) stopFailure = settled.stop;
        parentSignal?.removeEventListener('abort', onParentAbort);
        inFlight = null;
        const { result, outcome, error, stop } = settled;
        const after = sdk.getState();
        return json(200, {
            action_id: actionId,
            verb: b.verb,
            stop,                          // bounded game-side movement confirmation, or null
            outcome,                       // completed | deadline | cancelled | error
            result: result ?? null,        // the SDK's ActionResult (success, message, reason?, phase?)
            error: error ?? null,
            before: stamp(before),
            after: stamp(after),
            elapsed_ms: Date.now() - startedMs,
            note: 'result is the SDK\'s claim; confirm the effect from a fresh /observe before counting it',
        });
    }

    console.error(`[bridge] serving on http://127.0.0.1:${server.port} (token ${TOKEN ? 'required' : 'off'})`);
    // Live until the process is stopped (SIGTERM/SIGINT) or the bot disconnects.
    await new Promise<void>((_, reject) => parentSignal?.addEventListener('abort', () => {
        server.stop(true);
        reject(parentSignal.reason);
    }, { once: true }));
}, { printState: false, onDisconnect: 'error' });
