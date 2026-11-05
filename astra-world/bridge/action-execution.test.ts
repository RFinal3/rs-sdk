import { expect, test } from 'bun:test';
import { executionContext, executionWait, throwIfExecutionAborted } from '../../sdk/execution-context';
import { executeAction, stopMovement } from './action-execution';

function fakeSDK(ticks = [11, 12, 13]) {
    let state: any = { tick: 10, inGame: true, player: { worldX: 50, worldZ: 60, level: 0, lifeId: 1 } };
    const walks: unknown[] = [];
    const sdk = {
        getState: () => state,
        sendWalk: async (...args: unknown[]) => {
            throwIfExecutionAborted();
            walks.push(args);
            return { success: true, message: 'sent' };
        },
        waitForCondition: async (predicate: (s: any) => boolean) => {
            for (const tick of ticks) {
                state = { ...state, tick };
                if (predicate(state)) return state;
            }
            return executionWait<any>(() => () => {});
        },
    };
    return { sdk, walks };
}

test('cancel after dispatch sends stop outside aborted scope before settling', async () => {
    const { sdk, walks } = fakeSDK();
    const controller = new AbortController();
    const pending = executeAction(sdk, controller, 1000, async () => {
        await sdk.sendWalk(100, 100, true);
        return executionWait(() => () => {});
    });
    await Promise.resolve();
    controller.abort(new Error('cancel'));
    const result = await pending;
    expect(result.outcome).toBe('cancelled');
    expect(result.stop).toEqual({ confirmed: true });
    expect(walks).toEqual([[100, 100, true], [50, 60, false]]);
});

test('deadline performs the same stop and confirmation', async () => {
    const { sdk, walks } = fakeSDK();
    const result = await executeAction(sdk, new AbortController(), 5,
        () => executionWait(() => () => {}));
    expect(result.outcome).toBe('deadline');
    expect(result.stop?.confirmed).toBe(true);
    expect(walks).toEqual([[50, 60, false]]);
});

test('cleanup is independent even of an aborted parent context', async () => {
    const { sdk } = fakeSDK();
    const controller = new AbortController();
    controller.abort();
    const result = await executionContext.run({ signal: controller.signal }, () => stopMovement(sdk));
    expect(result.confirmed).toBe(true);
});

test('completed actions do not dispatch a stop', async () => {
    const { sdk, walks } = fakeSDK();
    const result = await executeAction(sdk, new AbortController(), 100, async () => ({ success: true }));
    expect(result.outcome).toBe('completed');
    expect(result.stop).toBeNull();
    expect(walks).toEqual([]);
});

test('duplicate or skipped ticks cannot prove three consecutive stationary ticks', async () => {
    for (const ticks of [[11, 11, 11], [11, 13, 15], []]) {
        const { sdk } = fakeSDK(ticks);
        expect((await stopMovement(sdk, 5)).confirmed).toBe(false);
    }
});

test('movement away from the stop tile is not confirmed', async () => {
    const { sdk } = fakeSDK();
    sdk.waitForCondition = async predicate => {
        for (let tick = 11; tick <= 15; tick++) {
            predicate({ ...sdk.getState(), tick, player: { ...sdk.getState().player, worldX: tick } });
        }
        return executionWait<any>(() => () => {});
    };
    expect((await stopMovement(sdk, 5)).confirmed).toBe(false);
});

test('dispatch failure, disconnect and hanging dispatch fail within the bound', async () => {
    const { sdk } = fakeSDK();
    sdk.sendWalk = async () => ({ success: false, message: 'disconnected' });
    expect(await stopMovement(sdk)).toEqual({ confirmed: false, error: 'Stop dispatch failed: disconnected' });
    sdk.sendWalk = () => executionWait(() => () => {});
    expect((await stopMovement(sdk, 5)).confirmed).toBe(false);
    sdk.getState = () => null;
    expect((await stopMovement(sdk)).confirmed).toBe(false);
});


test('a helper timeout returned as a failed result also stops dispatched movement', async () => {
    const { sdk, walks } = fakeSDK();
    const result = await executeAction(sdk, new AbortController(), 100, async () => {
        await sdk.sendWalk(100, 100, true);
        return { success: false, reason: 'timeout', message: 'helper deadline' };
    });
    expect(result.outcome).toBe('completed');
    expect(result.stop?.confirmed).toBe(true);
    expect(walks).toEqual([[100, 100, true], [50, 60, false]]);
});
