import { describe, expect, test } from 'bun:test';

import CollisionMap from '#/dash3d/CollisionMap.js';
import { ClientProt } from '#/io/ClientProt.js';
import Packet from '#/io/Packet.js';

import type { LiteClient } from './LiteClient.js';
import { findPathToTile, tryMoveTo } from './movement.js';

// Exercise real routing and packet encoding without a socket or cache archives.
function client(): LiteClient {
    return {
        ensureCollision() {},
        collision: [new CollisionMap()],
        minusedlevel: 0,
        localPlayer: { routeX: [50], routeZ: [50] },
        mapBuildBaseX: 3200,
        mapBuildBaseZ: 3200,
        out: Packet.alloc(1),
    } as unknown as LiteClient;
}

describe('movement packets', () => {
    test('a game click on the current tile emits a replacement route', () => {
        const c = client();
        expect(findPathToTile(c, 50, 50, false)).toBe(true);
        expect([...c.out.data.subarray(0, c.out.pos)]).toEqual([
            ClientProt.MOVE_GAMECLICK, 5, 0,
            3250 >> 8, 3250 & 0xff, 3250 >> 8, 3250 & 0xff,
        ]);
        expect(c.minimapFlagX).toBe(50);
        expect(c.minimapFlagZ).toBe(50);
    });

    test('interaction at the current tile still needs no movement packet', () => {
        const c = client();
        expect(tryMoveTo(c, 50, 50, 0, 0, 0, 0, 0)).toBe(true);
        expect(c.out.pos).toBe(0);
    });

    test('interaction already inside a footprint still needs no movement packet', () => {
        const c = client();
        expect(tryMoveTo(c, 48, 48, 5, 5, 0, 0, 0)).toBe(true);
        expect(c.out.pos).toBe(0);
    });
});
