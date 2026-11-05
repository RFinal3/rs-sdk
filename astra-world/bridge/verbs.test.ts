// bun test astra-world/bridge
import { describe, expect, test } from 'bun:test';
import { readFileSync } from 'fs';
import { join } from 'path';
import { VERBS, BridgeError, bindParams, exactText, getVerb } from './verbs';

// A real state captured from a local world on 2026-11-04 (after tutorial + one walk).
const state: any = JSON.parse(readFileSync(join(import.meta.dir, '../fixtures/state-02-after-walk.json'), 'utf8'));

function code(fn: () => unknown): string | null {
    try { fn(); return null; } catch (e) { return e instanceof BridgeError ? e.code : `non-bridge:${e}`; }
}

describe('bindParams', () => {
    test('walk_to accepts bounded ints and fills defaults', () => {
        const p = bindParams('walk_to', VERBS.walk_to, { x: 3222, z: 3218 }, state);
        expect(p).toEqual({ x: 3222, z: 3218, tolerance: 1, timeout_ms: 60_000 });
    });

    test('rejects unknown params, wrong types, out-of-range, missing', () => {
        expect(code(() => bindParams('walk_to', VERBS.walk_to, { x: 1, z: 2, code: 'x' }, state))).toBe('bad_params');
        expect(code(() => bindParams('walk_to', VERBS.walk_to, { x: '1', z: 2 }, state))).toBe('bad_params');
        expect(code(() => bindParams('walk_to', VERBS.walk_to, { x: 1.5, z: 2 }, state))).toBe('bad_params');
        expect(code(() => bindParams('walk_to', VERBS.walk_to, { x: 1, z: 2, tolerance: 99 }, state))).toBe('bad_params');
        expect(code(() => bindParams('walk_to', VERBS.walk_to, { x: 1 }, state))).toBe('bad_params');
        expect(code(() => bindParams('walk_to', VERBS.walk_to, [1, 2], state))).toBe('bad_params');
    });

    test('loc refs must match the current state exactly', () => {
        const loc = state.nearbyLocs.find((l: any) => l.optionsWithIndex.length > 0);
        const ok = bindParams('interact_loc', VERBS.interact_loc, { loc: { id: loc.id, x: loc.x, z: loc.z }, op: loc.optionsWithIndex[0].opIndex }, state);
        expect(ok.loc).toBe(loc);
        expect(code(() => bindParams('interact_loc', VERBS.interact_loc, { loc: { id: loc.id, x: loc.x + 1, z: loc.z }, op: 1 }, state))).toBe('target_not_found');
        expect(code(() => bindParams('interact_loc', VERBS.interact_loc, { loc: { name: loc.name }, op: 1 }, state))).toBe('bad_params');
    });

    test('op must be one the target actually offers', () => {
        const loc = state.nearbyLocs.find((l: any) => l.optionsWithIndex.length > 0 && l.optionsWithIndex.length < 5);
        const offered = new Set(loc.optionsWithIndex.map((o: any) => o.opIndex));
        const notOffered = [1, 2, 3, 4, 5].find(i => !offered.has(i))!;
        expect(code(() => bindParams('interact_loc', VERBS.interact_loc, { loc: { id: loc.id, x: loc.x, z: loc.z }, op: notOffered }, state))).toBe('bad_params');
    });

    test('npc refs need index AND type id (slot reuse guard)', () => {
        const npc = state.nearbyNpcs[0];
        expect(bindParams('talk_to', VERBS.talk_to, { npc: { index: npc.index, id: npc.id } }, state).npc).toBe(npc);
        expect(code(() => bindParams('talk_to', VERBS.talk_to, { npc: { index: npc.index, id: npc.id + 1 } }, state))).toBe('target_not_found');
        expect(code(() => bindParams('talk_to', VERBS.talk_to, { npc: { index: npc.index } }, state))).toBe('bad_params');
    });

    test('inventory refs need slot AND id', () => {
        const item = state.inventory[0];
        expect(bindParams('drop', VERBS.drop, { item: { slot: item.slot, id: item.id } }, state).item).toBe(item);
        expect(code(() => bindParams('drop', VERBS.drop, { item: { slot: item.slot, id: item.id + 1 } }, state))).toBe('target_not_found');
    });

    test('bank and shop verbs refuse when closed', () => {
        expect(code(() => bindParams('withdraw', VERBS.withdraw, { item: { slot: 0, id: 1 } }, state))).toBe('precondition');
        expect(code(() => bindParams('buy', VERBS.buy, { item: { id: 1 } }, state))).toBe('precondition');
        const item = state.inventory[0];
        expect(code(() => bindParams('deposit', VERBS.deposit, { item: { slot: item.slot, id: item.id } }, state))).toBe('precondition');
        expect(code(() => bindParams('sell', VERBS.sell, { item: { slot: item.slot, id: item.id } }, state))).toBe('precondition');
    });

    test('dialog_choose refuses with no dialog open', () => {
        expect(code(() => bindParams('dialog_choose', VERBS.dialog_choose, { index: 1 }, state))).toBe('precondition');
    });

    test('string params are bounded', () => {
        expect(code(() => bindParams('smith_at_anvil', VERBS.smith_at_anvil, { product: 'x'.repeat(41) }, state))).toBe('bad_params');
        expect(code(() => bindParams('smith_at_anvil', VERBS.smith_at_anvil, { product: '' }, state))).toBe('bad_params');
    });
});

test('every verb has a description and only known param types', () => {
    const known = new Set(['int', 'bool', 'loc', 'npc', 'inv', 'ground', 'bank', 'shop', 'string']);
    for (const [name, v] of Object.entries(VERBS)) {
        expect(v.description.length).toBeGreaterThan(5);
        for (const spec of Object.values(v.params)) expect(known.has(spec.type)).toBe(true);
        expect(name).toMatch(/^[a-z_]+$/);
    }
});

test('getVerb ignores prototype keys and non-strings', () => {
    expect(getVerb('walk_to')).toBe(VERBS.walk_to);
    expect(getVerb('constructor')).toBeUndefined();
    expect(getVerb('__proto__')).toBeUndefined();
    expect(getVerb(42)).toBeUndefined();
});

test('exactText escapes regex metacharacters', () => {
    expect(exactText('a.b').test('a.b')).toBe(true);
    expect(exactText('a.b').test('axb')).toBe(false);
    expect(exactText('(.*)').test('anything')).toBe(false);
});
