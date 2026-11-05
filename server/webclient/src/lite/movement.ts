// Client-side routing: the BFS from Client.ts tryMove(), plus the MOVE_* packet
// it emits.
//
// This has to exist. 274 runs with clientRoutefinder=true, which means
// MoveClickHandler replays the client's waypoint list verbatim and the server
// does NOT pathfind to interaction targets (see PATCHES.md "Walk-before-op").
// Sending a bare destination makes the player walk into the first wall.
//
// The BFS runs over the real CollisionMap class populated by CollisionBuilder, so
// testWall/testWDecor/testLoc reach rules match the browser client exactly - the
// difference between standing where the server accepts an OPLOC and getting
// "I can't reach that!".

import CollisionMap, { BuildArea } from '#/dash3d/CollisionMap.js';
import { CollisionFlag } from '#/dash3d/CollisionFlag.js';
import { DirectionFlag } from '#/dash3d/DirectionFlag.js';
import { LocShape } from '#/dash3d/LocShape.js';

import { ClientProt } from '#/io/ClientProt.js';

import type { LiteClient } from './LiteClient.js';

const ROUTE_BUFFER = 4000;

const routeX = new Int32Array(ROUTE_BUFFER);
const routeZ = new Int32Array(ROUTE_BUFFER);
const dirMap = new Int32Array(BuildArea.SIZE * BuildArea.SIZE);
const distMap = new Int32Array(BuildArea.SIZE * BuildArea.SIZE);

/** MOVE_GAMECLICK - walk to a tile. */
export function findPathToTile(c: LiteClient, destX: number, destZ: number, running: boolean): boolean {
    return route(c, destX, destZ, true, 0, 0, 0, 0, 0, 0, running);
}

/** MOVE_OPCLICK - walk into reach of a loc, then the caller sends the OP packet. */
export function tryMoveTo(c: LiteClient, destX: number, destZ: number, locWidth: number, locLength: number, locAngle: number, locShape: number, forceapproach: number): boolean {
    return route(c, destX, destZ, false, locWidth, locLength, locAngle, locShape, forceapproach, 2, true);
}

/**
 * Breadth-first search over the build area, then emit the resulting turn list.
 * Structure follows Client.ts tryMove() step for step; the eight neighbour
 * expansions and their diagonal corner-cutting guards are load-bearing.
 */
function route(
    c: LiteClient,
    dx: number,
    dz: number,
    tryNearest: boolean,
    locWidth: number,
    locLength: number,
    locAngle: number,
    locShape: number,
    forceapproach: number,
    type: number,
    running = true
): boolean {
    // Fold in any door/loc changes the server has sent since the last route.
    c.ensureCollision();

    const collisionMap = c.collision[c.minusedlevel];
    if (!collisionMap || !c.localPlayer) {
        return false;
    }

    const srcX = c.localPlayer.routeX[0];
    const srcZ = c.localPlayer.routeZ[0];

    // The normal BFS performs these exact reach checks on its first queue
    // element, after clearing two full BuildArea-sized scratch maps. Hot
    // interactions such as the KBD spawn attack are deliberately staged
    // inside the target footprint, so return the identical "already in
    // reach" result before those O(scene) fills. As in the existing
    // length<=0 branch below, no MOVE_OPCLICK packet is required here; the
    // caller immediately emits the interaction op.
    // A game click must still replace the server's queued route/interaction,
    // even when its destination is the current tile (the bridge's stop request).
    // Only interaction routing can skip the movement packet here.
    if (type === 2 && srcX === dx && srcZ === dz) {
        return true;
    }
    if (locShape !== LocShape.WALL_STRAIGHT) {
        if ((locShape < LocShape.WALLDECOR_STRAIGHT_OFFSET
            || locShape === LocShape.CENTREPIECE_STRAIGHT)
            && collisionMap.testWall(srcX, srcZ, dx, dz, locShape - 1, locAngle)) {
            return true;
        }
        if (locShape < LocShape.CENTREPIECE_STRAIGHT
            && collisionMap.testWDecor(srcX, srcZ, dx, dz, locShape - 1, locAngle)) {
            return true;
        }
    }
    if (locWidth !== 0 && locLength !== 0
        && collisionMap.testLoc(srcX, srcZ, dx, dz, locWidth, locLength, forceapproach)) {
        return true;
    }

    dirMap.fill(0);
    distMap.fill(99999999);

    let x = srcX;
    let z = srcZ;

    const srcIndex = CollisionMap.index(srcX, srcZ);
    dirMap[srcIndex] = 99;
    distMap[srcIndex] = 0;

    let steps = 0;
    let length = 0;
    routeX[steps] = srcX;
    routeZ[steps++] = srcZ;

    let arrived = false;
    let bufferSize = routeX.length;
    const flags = collisionMap.flags;

    while (length !== steps) {
        x = routeX[length];
        z = routeZ[length];
        length = (length + 1) % bufferSize;

        if (x === dx && z === dz) {
            arrived = true;
            break;
        }

        if (locShape !== LocShape.WALL_STRAIGHT) {
            if ((locShape < LocShape.WALLDECOR_STRAIGHT_OFFSET || locShape === LocShape.CENTREPIECE_STRAIGHT) && collisionMap.testWall(x, z, dx, dz, locShape - 1, locAngle)) {
                arrived = true;
                break;
            }
            if (locShape < LocShape.CENTREPIECE_STRAIGHT && collisionMap.testWDecor(x, z, dx, dz, locShape - 1, locAngle)) {
                arrived = true;
                break;
            }
        }

        if (locWidth !== 0 && locLength !== 0 && collisionMap.testLoc(x, z, dx, dz, locWidth, locLength, forceapproach)) {
            arrived = true;
            break;
        }

        const nextCost = distMap[CollisionMap.index(x, z)] + 1;

        let index = CollisionMap.index(x - 1, z);
        if (x > 0 && dirMap[index] === 0 && (flags[index] & CollisionFlag.PL_WALK_E) === CollisionFlag._OPEN) {
            routeX[steps] = x - 1;
            routeZ[steps] = z;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 2;
            distMap[index] = nextCost;
        }

        index = CollisionMap.index(x + 1, z);
        if (x < BuildArea.SIZE - 1 && dirMap[index] === 0 && (flags[index] & CollisionFlag.PL_WALK_W) === CollisionFlag._OPEN) {
            routeX[steps] = x + 1;
            routeZ[steps] = z;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 8;
            distMap[index] = nextCost;
        }

        index = CollisionMap.index(x, z - 1);
        if (z > 0 && dirMap[index] === 0 && (flags[index] & CollisionFlag.PL_WALK_N) === CollisionFlag._OPEN) {
            routeX[steps] = x;
            routeZ[steps] = z - 1;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 1;
            distMap[index] = nextCost;
        }

        index = CollisionMap.index(x, z + 1);
        if (z < BuildArea.SIZE - 1 && dirMap[index] === 0 && (flags[index] & CollisionFlag.PL_WALK_S) === CollisionFlag._OPEN) {
            routeX[steps] = x;
            routeZ[steps] = z + 1;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 4;
            distMap[index] = nextCost;
        }

        index = CollisionMap.index(x - 1, z - 1);
        if (
            x > 0 &&
            z > 0 &&
            dirMap[index] === 0 &&
            (flags[index] & CollisionFlag.PL_WALK_NE) === 0 &&
            (flags[CollisionMap.index(x - 1, z)] & CollisionFlag.PL_WALK_E) === CollisionFlag._OPEN &&
            (flags[CollisionMap.index(x, z - 1)] & CollisionFlag.PL_WALK_N) === CollisionFlag._OPEN
        ) {
            routeX[steps] = x - 1;
            routeZ[steps] = z - 1;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 3;
            distMap[index] = nextCost;
        }

        index = CollisionMap.index(x + 1, z - 1);
        if (
            x < BuildArea.SIZE - 1 &&
            z > 0 &&
            dirMap[index] === 0 &&
            (flags[index] & CollisionFlag.PL_WALK_NW) === 0 &&
            (flags[CollisionMap.index(x + 1, z)] & CollisionFlag.PL_WALK_W) === CollisionFlag._OPEN &&
            (flags[CollisionMap.index(x, z - 1)] & CollisionFlag.PL_WALK_N) === CollisionFlag._OPEN
        ) {
            routeX[steps] = x + 1;
            routeZ[steps] = z - 1;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 9;
            distMap[index] = nextCost;
        }

        index = CollisionMap.index(x - 1, z + 1);
        if (
            x > 0 &&
            z < BuildArea.SIZE - 1 &&
            dirMap[index] === 0 &&
            (flags[index] & CollisionFlag.PL_WALK_SE) === 0 &&
            (flags[CollisionMap.index(x - 1, z)] & CollisionFlag.PL_WALK_E) === CollisionFlag._OPEN &&
            (flags[CollisionMap.index(x, z + 1)] & CollisionFlag.PL_WALK_S) === CollisionFlag._OPEN
        ) {
            routeX[steps] = x - 1;
            routeZ[steps] = z + 1;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 6;
            distMap[index] = nextCost;
        }

        index = CollisionMap.index(x + 1, z + 1);
        if (
            x < BuildArea.SIZE - 1 &&
            z < BuildArea.SIZE - 1 &&
            dirMap[index] === 0 &&
            (flags[index] & CollisionFlag.PL_WALK_SW) === 0 &&
            (flags[CollisionMap.index(x + 1, z)] & CollisionFlag.PL_WALK_W) === CollisionFlag._OPEN &&
            (flags[CollisionMap.index(x, z + 1)] & CollisionFlag.PL_WALK_S) === CollisionFlag._OPEN
        ) {
            routeX[steps] = x + 1;
            routeZ[steps] = z + 1;
            steps = (steps + 1) % bufferSize;
            dirMap[index] = 12;
            distMap[index] = nextCost;
        }
    }

    if (!arrived) {
        if (tryNearest) {
            let min = 100;
            for (let padding = 1; padding < 2; padding++) {
                for (let px = dx - padding; px <= dx + padding; px++) {
                    for (let pz = dz - padding; pz <= dz + padding; pz++) {
                        const index = CollisionMap.index(px, pz);
                        if (px >= 0 && pz >= 0 && px < BuildArea.SIZE && pz < BuildArea.SIZE && distMap[index] < min) {
                            min = distMap[index];
                            x = px;
                            z = pz;
                            arrived = true;
                        }
                    }
                }
                if (arrived) {
                    break;
                }
            }
        }
        if (!arrived) {
            return false;
        }
    }

    // Walk the direction map backwards from the destination, recording a
    // waypoint only where the direction changes (that's what "turns" means).
    length = 0;
    routeX[length] = x;
    routeZ[length++] = z;

    let dir = dirMap[CollisionMap.index(x, z)];
    let next = dir;
    while (x !== srcX || z !== srcZ) {
        if (next !== dir) {
            dir = next;
            routeX[length] = x;
            routeZ[length++] = z;
        }

        if ((next & DirectionFlag.EAST) !== 0) {
            x++;
        } else if ((next & DirectionFlag.WEST) !== 0) {
            x--;
        }

        if ((next & DirectionFlag.NORTH) !== 0) {
            z++;
        } else if ((next & DirectionFlag.SOUTH) !== 0) {
            z--;
        }

        next = dirMap[CollisionMap.index(x, z)];
    }

    if (length <= 0) {
        // Already standing on the target/in reach - nothing to send.
        return true;
    }

    bufferSize = Math.min(length, 25); // server caps a pathfind request at 25 turns
    length--;

    const startX = routeX[length];
    const startZ = routeZ[length];

    if (type === 0) {
        c.out.p1Enc(ClientProt.MOVE_GAMECLICK);
        c.out.p1(bufferSize + bufferSize + 3);
    } else if (type === 2) {
        c.out.p1Enc(ClientProt.MOVE_OPCLICK);
        c.out.p1(bufferSize + bufferSize + 3);
    }

    c.out.p1(running ? 1 : 0);
    c.out.p2(startX + c.mapBuildBaseX);
    c.out.p2(startZ + c.mapBuildBaseZ);

    c.minimapFlagX = routeX[0];
    c.minimapFlagZ = routeZ[0];

    for (let i = 1; i < bufferSize; i++) {
        length--;
        c.out.p1(routeX[length] - startX);
        c.out.p1(routeZ[length] - startZ);
    }

    return true;
}
