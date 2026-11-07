// rs-sdk / astra-world: staff (admin) access on a private world without a login server.
//
// LoginThread hard-codes staffmodlevel 0 when the login server is off, because the same code
// runs the public demo server. On a private world the owner can name staff accounts with
//   NODE_STAFF_USERNAMES=name1,name2   and   NODE_STAFF_PASSWORD=<at least 8 characters>
// A named account gets STAFF_LEVEL only when it logs in with that password. The name alone is
// never enough: without a login server, passwords are not otherwise checked, and anything that
// can reach the world (e.g. the player container) could log in as any name.
import { timingSafeEqual } from 'crypto';

export const STAFF_LEVEL = 3;              // ::tele, ::teleto, ::setvis, ::setstat, ::give; not the level-4 dev commands
export const MIN_STAFF_PASSWORD_LENGTH = 8;

export type StaffConfig = { staffUsernames: string[]; staffPassword: string };

export function parseStaffUsernames(value: string | undefined | null): string[] {
    if (!value) return [];
    return value.split(',').map(s => s.trim().toLowerCase().replace(/ /g, '_')).filter(s => s.length > 0);
}

/** Staff is configured only with at least one name and a long-enough password. */
export function staffConfigured(cfg: StaffConfig): boolean {
    return cfg.staffUsernames.length > 0 && cfg.staffPassword.length >= MIN_STAFF_PASSWORD_LENGTH;
}

function samePassword(a: string, b: string): boolean {
    const x = Buffer.from(a, 'utf8');
    const y = Buffer.from(b, 'utf8');
    return x.length === y.length && timingSafeEqual(x, y);
}

/** The staffmodlevel for a login on a world without a login server. */
export function staffLevelForLogin(username: string, password: string | undefined | null, cfg: StaffConfig): number {
    if (!staffConfigured(cfg)) return 0;
    if (!cfg.staffUsernames.includes(username.toLowerCase())) return 0;
    if (typeof password !== 'string' || !samePassword(password, cfg.staffPassword)) return 0;
    return STAFF_LEVEL;
}
