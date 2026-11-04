/**
 * The fixed verb table: the only things Astra's harness can make the character do.
 *
 * Rules (see astra-world/HANDOFF.md):
 *  - No code strings, no caller-supplied regular expressions. Targets are references
 *    ({id, x, z}, {index, id}, {slot, id}) that must match an object in the CURRENT
 *    state, or the action is refused before anything is sent.
 *  - Options are op indices taken from the target's own optionsWithIndex.
 *  - Every verb runs under a deadline. Cancellation comes from the bridge.
 *
 * Adding a verb: add it here with a param spec, add a test in verbs.test.ts.
 */
import type { BotActions } from '../../sdk/actions';
import type { BotSDK } from '../../sdk/index';
import type {
    BotWorldState, NearbyLoc, NearbyNpc, InventoryItem, GroundItem, BankItem, ShopItem,
} from '../../sdk/types';

export class BridgeError extends Error {
    constructor(public code: string, message: string) {
        super(message);
    }
}

export type ParamSpec =
    | { type: 'int'; min: number; max: number; required?: boolean; default?: number }
    | { type: 'bool'; required?: boolean; default?: boolean }
    | { type: 'loc'; required?: boolean }          // {id, x, z} from state.nearbyLocs
    | { type: 'npc'; required?: boolean }          // {index, id} from state.nearbyNpcs
    | { type: 'inv'; required?: boolean }          // {slot, id} from state.inventory
    | { type: 'ground'; required?: boolean }       // {id, x, z} from state.groundItems
    | { type: 'bank'; required?: boolean }         // {slot, id} from state.bank.items (bank open)
    | { type: 'shop'; required?: boolean }         // {id} from state.shop.shopItems (shop open)
    | { type: 'string'; maxLen: number; required?: boolean; oneOf?: string[] };

export interface VerbCtx {
    bot: BotActions;
    sdk: BotSDK;
    state: BotWorldState;
}

export interface Verb {
    description: string;
    params: Record<string, ParamSpec>;
    /** Extra cross-parameter checks against current state (e.g. option offered by target). */
    check?: (p: Record<string, any>, state: BotWorldState) => void;
    run: (ctx: VerbCtx, p: Record<string, any>) => Promise<any>;
}

const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);

function refInt(ref: any, key: string, verb: string, param: string): number {
    if (!ref || typeof ref !== 'object' || !isInt(ref[key])) {
        throw new BridgeError('bad_params', `${verb}.${param} needs integer '${key}'`);
    }
    return ref[key];
}

export function resolveLoc(state: BotWorldState, ref: any, verb = '?', param = 'loc'): NearbyLoc {
    const id = refInt(ref, 'id', verb, param), x = refInt(ref, 'x', verb, param), z = refInt(ref, 'z', verb, param);
    const loc = (state.nearbyLocs ?? []).find(l => l.id === id && l.x === x && l.z === z);
    if (!loc) throw new BridgeError('target_not_found', `${verb}: no loc id=${id} at (${x},${z}) in current state`);
    return loc;
}

export function resolveNpc(state: BotWorldState, ref: any, verb = '?', param = 'npc'): NearbyNpc {
    // index is a world slot and gets reused; requiring the type id as well catches most reuse.
    const index = refInt(ref, 'index', verb, param), id = refInt(ref, 'id', verb, param);
    const npc = (state.nearbyNpcs ?? []).find(n => n.index === index && n.id === id);
    if (!npc) throw new BridgeError('target_not_found', `${verb}: no npc index=${index} id=${id} in current state`);
    return npc;
}

export function resolveInv(state: BotWorldState, ref: any, verb = '?', param = 'item'): InventoryItem {
    const slot = refInt(ref, 'slot', verb, param), id = refInt(ref, 'id', verb, param);
    const item = (state.inventory ?? []).find(i => i.slot === slot && i.id === id);
    if (!item) throw new BridgeError('target_not_found', `${verb}: no inventory item id=${id} in slot ${slot}`);
    return item;
}

export function resolveGround(state: BotWorldState, ref: any, verb = '?', param = 'item'): GroundItem {
    const id = refInt(ref, 'id', verb, param), x = refInt(ref, 'x', verb, param), z = refInt(ref, 'z', verb, param);
    const item = (state.groundItems ?? []).find(g => g.id === id && g.x === x && g.z === z);
    if (!item) throw new BridgeError('target_not_found', `${verb}: no ground item id=${id} at (${x},${z})`);
    return item;
}

export function resolveBank(state: BotWorldState, ref: any, verb = '?', param = 'item'): BankItem {
    if (!state.bank?.isOpen) throw new BridgeError('precondition', `${verb}: bank is not open`);
    const slot = refInt(ref, 'slot', verb, param), id = refInt(ref, 'id', verb, param);
    const item = state.bank.items.find(b => b.slot === slot && b.id === id);
    if (!item) throw new BridgeError('target_not_found', `${verb}: no bank item id=${id} in slot ${slot}`);
    return item;
}

export function resolveShop(state: BotWorldState, ref: any, verb = '?', param = 'item'): ShopItem {
    if (!state.shop?.isOpen) throw new BridgeError('precondition', `${verb}: shop is not open`);
    const id = refInt(ref, 'id', verb, param);
    const item = state.shop.shopItems.find(s => s.id === id);
    if (!item) throw new BridgeError('target_not_found', `${verb}: shop does not stock id=${id}`);
    return item;
}

const RESOLVERS: Record<string, (s: BotWorldState, r: any, v: string, p: string) => any> = {
    loc: resolveLoc, npc: resolveNpc, inv: resolveInv, ground: resolveGround, bank: resolveBank, shop: resolveShop,
};

/** Validate raw params against a verb's spec; resolve target refs against current state. */
export function bindParams(verbName: string, verb: Verb, raw: unknown, state: BotWorldState): Record<string, any> {
    if (raw !== undefined && (raw === null || typeof raw !== 'object' || Array.isArray(raw))) {
        throw new BridgeError('bad_params', `${verbName}: params must be an object`);
    }
    const input = (raw ?? {}) as Record<string, unknown>;
    for (const key of Object.keys(input)) {
        if (!(key in verb.params)) throw new BridgeError('bad_params', `${verbName}: unknown param '${key}'`);
    }
    const out: Record<string, any> = {};
    for (const [name, spec] of Object.entries(verb.params)) {
        const v = input[name];
        if (v === undefined) {
            if ('default' in spec && spec.default !== undefined) { out[name] = spec.default; continue; }
            if (spec.required) throw new BridgeError('bad_params', `${verbName}: missing '${name}'`);
            continue;
        }
        switch (spec.type) {
            case 'int':
                if (!isInt(v) || v < spec.min || v > spec.max) {
                    throw new BridgeError('bad_params', `${verbName}.${name} must be an integer in [${spec.min}, ${spec.max}]`);
                }
                out[name] = v; break;
            case 'bool':
                if (typeof v !== 'boolean') throw new BridgeError('bad_params', `${verbName}.${name} must be boolean`);
                out[name] = v; break;
            case 'string':
                if (typeof v !== 'string' || v.length === 0 || v.length > spec.maxLen) {
                    throw new BridgeError('bad_params', `${verbName}.${name} must be a non-empty string of at most ${spec.maxLen}`);
                }
                if (spec.oneOf && !spec.oneOf.includes(v)) {
                    throw new BridgeError('bad_params', `${verbName}.${name} must be one of ${spec.oneOf.join(', ')}`);
                }
                out[name] = v; break;
            default: {
                const resolve = RESOLVERS[spec.type];
                if (!resolve) throw new BridgeError('internal', `no resolver for ${spec.type}`);
                out[name] = resolve(state, v, verbName, name);
            }
        }
    }
    verb.check?.(out, state);
    return out;
}

function requireOption(target: { name: string; optionsWithIndex: { opIndex: number; text: string }[] }, op: number, verb: string) {
    if (!target.optionsWithIndex.some(o => o.opIndex === op)) {
        const offered = target.optionsWithIndex.map(o => `${o.opIndex}=${o.text}`).join(', ') || 'none';
        throw new BridgeError('bad_params', `${verb}: ${target.name} does not offer op ${op} (offers: ${offered})`);
    }
}

/** Literal-text matcher for SDK helpers that take a string/RegExp: escapes everything. */
export function exactText(text: string): RegExp {
    return new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'i');
}

const timeoutParam = (def: number, max = 300_000): ParamSpec => ({ type: 'int', min: 500, max, default: def });

export const VERBS = {
    skip_tutorial: {
        description: 'Leave the tutorial if the character is in it.',
        params: {},
        run: ({ bot }) => bot.skipTutorial(),
    },
    walk_to: {
        description: 'Walk to world tile (x, z) with pathfinding; arrives when within tolerance tiles.',
        params: {
            x: { type: 'int', min: 0, max: 16_383, required: true },
            z: { type: 'int', min: 0, max: 16_383, required: true },
            tolerance: { type: 'int', min: 0, max: 10, default: 1 },
            timeout_ms: timeoutParam(60_000),
        },
        run: ({ bot }, p) => bot.walkTo(p.x, p.z, p.tolerance, { timeout: p.timeout_ms }),
    },
    interact_loc: {
        description: 'Use one of a loc\'s own options (op index from its optionsWithIndex).',
        params: { loc: { type: 'loc', required: true }, op: { type: 'int', min: 1, max: 5, required: true } },
        check: p => requireOption(p.loc, p.op, 'interact_loc'),
        run: ({ bot }, p) => bot.interactLoc(p.loc, p.op),
    },
    interact_npc: {
        description: 'Use one of an NPC\'s own options (e.g. fishing spot "Net", shop "Trade").',
        params: { npc: { type: 'npc', required: true }, op: { type: 'int', min: 1, max: 5, required: true } },
        check: p => requireOption(p.npc, p.op, 'interact_npc'),
        run: ({ bot }, p) => bot.interactNpc(p.npc, p.op),
    },
    talk_to: {
        description: 'Walk to an NPC and open dialogue.',
        params: { npc: { type: 'npc', required: true } },
        run: ({ bot }, p) => bot.talkTo(p.npc),
    },
    dialog_choose: {
        description: 'Click an option in the open dialogue by its index in state.dialog.options. 0 = continue.',
        params: { index: { type: 'int', min: 0, max: 20, required: true } },
        check: (p, state) => {
            if (!state.dialog?.isOpen) throw new BridgeError('precondition', 'dialog_choose: no dialog open');
            if (p.index !== 0 && !state.dialog.options.some(o => o.index === p.index)) {
                throw new BridgeError('bad_params', `dialog_choose: option ${p.index} not offered`);
            }
        },
        run: async ({ sdk }, p) => {
            await sdk.sendClickDialog(p.index);
            await sdk.waitForTicks(1);
            return { success: true, message: `clicked dialog option ${p.index}`, phase: 'dispatch' };
        },
    },
    close_interface: {
        description: 'Close an open modal (bank, quest scroll, level-up, ...).',
        params: {},
        run: ({ bot }) => bot.closeInterface(),
    },
    open_door: {
        description: 'Open a door or gate (walks to it).',
        params: { loc: { type: 'loc', required: true } },
        run: ({ bot }, p) => bot.openDoor(p.loc),
    },
    mine_rock: {
        description: 'Mine a rock; waits for ore and Mining XP.',
        params: { loc: { type: 'loc', required: true }, timeout_ms: timeoutParam(30_000) },
        run: ({ bot }, p) => bot.mineRock(p.loc, { timeout: p.timeout_ms }),
    },
    chop_tree: {
        description: 'Chop a tree; waits for logs.',
        params: { loc: { type: 'loc', required: true } },
        run: ({ bot }, p) => bot.chopTree(p.loc),
    },
    burn_logs: {
        description: 'Light logs with a tinderbox.',
        params: { item: { type: 'inv' } },
        run: ({ bot }, p) => bot.burnLogs(p.item),
    },
    use_item_on_loc: {
        description: 'Use an inventory item on a loc (fish on range, ore on furnace).',
        params: { item: { type: 'inv', required: true }, loc: { type: 'loc', required: true }, timeout_ms: timeoutParam(15_000) },
        run: ({ bot }, p) => bot.useItemOnLoc(p.item, p.loc, { timeout: p.timeout_ms }),
    },
    use_item_on_npc: {
        description: 'Use an inventory item on an NPC.',
        params: { item: { type: 'inv', required: true }, npc: { type: 'npc', required: true }, timeout_ms: timeoutParam(15_000) },
        run: ({ bot }, p) => bot.useItemOnNpc(p.item, p.npc, { timeout: p.timeout_ms }),
    },
    smith_at_anvil: {
        description: 'Smith at an adjacent anvil; product is the visible product name (e.g. "dagger").',
        params: { product: { type: 'string', maxLen: 40, required: true }, timeout_ms: timeoutParam(30_000) },
        run: ({ bot }, p) => bot.smithAtAnvil(p.product, { timeout: p.timeout_ms }),
    },
    pickup: {
        description: 'Pick up a ground item.',
        params: { item: { type: 'ground', required: true } },
        run: ({ bot }, p) => bot.pickupItem(p.item),
    },
    drop: {
        description: 'Drop an inventory item (amount -1 = all of that item).',
        params: { item: { type: 'inv', required: true }, amount: { type: 'int', min: -1, max: 28, default: -1 } },
        run: ({ bot }, p) => bot.dropItem(p.item, p.amount === -1 ? 'all' : p.amount),
    },
    eat: {
        description: 'Eat food (a specific item, or the first edible one).',
        params: { item: { type: 'inv' } },
        run: ({ bot }, p) => bot.eatFood(p.item),
    },
    equip: {
        description: 'Equip an inventory item.',
        params: { item: { type: 'inv', required: true } },
        run: ({ bot }, p) => bot.equipItem(p.item),
    },
    attack: {
        description: 'Attack an NPC (walks to it).',
        params: { npc: { type: 'npc', required: true }, timeout_ms: timeoutParam(5_000, 30_000) },
        run: ({ bot }, p) => bot.attack(p.npc, p.timeout_ms),
    },
    open_bank: {
        description: 'Open the nearest bank booth or banker.',
        params: { timeout_ms: timeoutParam(10_000, 60_000) },
        run: ({ bot }, p) => bot.openBank(p.timeout_ms),
    },
    close_bank: {
        description: 'Close the bank.',
        params: {},
        run: ({ bot }) => bot.closeBank(),
    },
    deposit: {
        description: 'Deposit an inventory item (amount -1 = all of it). Bank must be open.',
        params: { item: { type: 'inv', required: true }, amount: { type: 'int', min: -1, max: 2_147_483_647, default: -1 } },
        check: (_p, state) => { if (!state.bank?.isOpen) throw new BridgeError('precondition', 'deposit: bank is not open'); },
        run: ({ bot }, p) => bot.depositItem(p.item, p.amount),
    },
    withdraw: {
        description: 'Withdraw a bank item. Bank must be open.',
        params: { item: { type: 'bank', required: true }, amount: { type: 'int', min: 1, max: 2_147_483_647, default: 1 }, as_note: { type: 'bool', default: false } },
        run: ({ bot }, p) => bot.withdrawItem(p.item, p.amount, { asNote: p.as_note }),
    },
    open_shop: {
        description: 'Open a shop by trading with this NPC.',
        params: { npc: { type: 'npc', required: true } },
        run: ({ bot }, p) => bot.openShop(p.npc),
    },
    buy: {
        description: 'Buy from the open shop.',
        params: { item: { type: 'shop', required: true }, amount: { type: 'int', min: 1, max: 1_000, default: 1 } },
        run: ({ bot }, p) => bot.buyFromShop(p.item, p.amount),
    },
    sell: {
        description: 'Sell an inventory item to the open shop.',
        params: { item: { type: 'inv', required: true }, amount: { type: 'int', min: 1, max: 1_000, default: 1 } },
        check: (_p, state) => { if (!state.shop?.isOpen) throw new BridgeError('precondition', 'sell: shop is not open'); },
        run: ({ bot }, p) => bot.sellToShop(p.item, p.amount),
    },
    close_shop: {
        description: 'Close the shop.',
        params: {},
        run: ({ bot }) => bot.closeShop(),
    },
    wait_idle: {
        description: 'Wait until the character stops moving.',
        params: { timeout_ms: timeoutParam(10_000, 60_000) },
        run: async ({ bot }, p) => { await bot.waitForIdle(p.timeout_ms); return { success: true, message: 'idle', phase: 'observation' }; },
    },
} satisfies Record<string, Verb>;

/** Look up a verb by untrusted name. */
export function getVerb(name: unknown): Verb | undefined {
    return typeof name === 'string' && Object.hasOwn(VERBS, name) ? (VERBS as Record<string, Verb>)[name] : undefined;
}

/** Machine-readable description of the verb table, served at GET /verbs. */
export function describeVerbs() {
    return Object.fromEntries(Object.entries(VERBS).map(([name, v]) => [name, { description: v.description, params: v.params }]));
}
