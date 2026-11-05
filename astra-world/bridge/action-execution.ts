import type { BotSDK } from '../../sdk/index';
import { executionContext, withExecutionDeadline } from '../../sdk/execution-context';

export type StopResult = { confirmed: boolean; error?: string };
type StopSDK = Pick<BotSDK, 'getState' | 'sendWalk' | 'waitForCondition'>;
export const STOP_TIMEOUT_MS = 5_000;

/** A separate, bounded scope: the action's signal has already been aborted. */
export async function stopMovement(sdk: StopSDK, timeout = STOP_TIMEOUT_MS): Promise<StopResult> {
    try {
        return await executionContext.run({ signal: new AbortController().signal }, () =>
            withExecutionDeadline<StopResult>(timeout, async () => {
                const state = sdk.getState();
                if (!state?.inGame || !state.player) throw new Error('No in-game state for stop');
                const { worldX: x, worldZ: z, level, lifeId } = state.player;
                // A MOVE_GAMECLICK replaces the server's queued route/interaction.
                // Unlike bot.walkTo, this must dispatch even when already at the tile.
                const sent = await sdk.sendWalk(x, z, false);
                if (!sent.success) throw new Error(`Stop dispatch failed: ${sent.message}`);
                let tick = sdk.getState()?.tick ?? state.tick;
                let stableTicks = 0;
                await sdk.waitForCondition(s => {
                    if (!s.inGame || !s.player || s.player.lifeId !== lifeId) throw new Error('Lost player while stopping');
                    if (s.tick <= tick) return false;
                    const consecutive = s.tick === tick + 1;
                    tick = s.tick;
                    const atTarget = s.player.worldX === x && s.player.worldZ === z && s.player.level === level;
                    stableTicks = atTarget ? (consecutive ? stableTicks + 1 : 1) : 0;
                    return stableTicks >= 3;
                }, timeout);
                return { confirmed: true };
            }, () => ({ confirmed: false, error: 'Stop confirmation timed out' })));
    } catch (error) {
        return { confirmed: false, error: String((error as Error)?.message ?? error) };
    }
}

export async function executeAction(
    sdk: StopSDK, controller: AbortController, deadline: number, work: () => Promise<any>,
    stopTimeout = STOP_TIMEOUT_MS,
): Promise<{ result: any; outcome: 'completed' | 'deadline' | 'cancelled' | 'error'; error: string | null; stop: StopResult | null }> {
    let result: any = null;
    let outcome: 'completed' | 'deadline' | 'cancelled' | 'error' = 'completed';
    let error: string | null = null;
    try {
        result = await executionContext.run({ signal: controller.signal }, () =>
            withExecutionDeadline(deadline, work, () => {
                outcome = 'deadline';
                return { success: false, message: `deadline ${deadline}ms exceeded`, reason: 'deadline' };
            }));
        // Cancellation may race with an action resolving normally.
        if (controller.signal.aborted) outcome = 'cancelled';
    } catch (e) {
        outcome = controller.signal.aborted ? 'cancelled' : 'error';
        error = String((e as Error)?.message ?? e);
    }
    // Errors can also leave a dispatched route running. Keep admission locked
    // until cleanup finishes; callers must latch closed if confirmation fails.
    const stop = outcome === 'completed' && result?.success !== false
        ? null : await stopMovement(sdk, stopTimeout);
    return { result, outcome, error, stop };
}
