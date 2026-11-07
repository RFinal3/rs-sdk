import { describe, expect, test } from 'bun:test';

import { parseStaffUsernames, STAFF_LEVEL, staffConfigured, staffLevelForLogin } from '#/server/login/staff.js';

const cfg = { staffUsernames: parseStaffUsernames(' Rfinal3 , other name '), staffPassword: 'correct horse' };

describe('astra-world staff logins', () => {
    test('names are normalised like safe usernames', () => {
        expect(cfg.staffUsernames).toEqual(['rfinal3', 'other_name']);
        expect(parseStaffUsernames('')).toEqual([]);
        expect(parseStaffUsernames(undefined)).toEqual([]);
    });

    test('a named account with the staff password gets staff', () => {
        expect(staffLevelForLogin('rfinal3', 'correct horse', cfg)).toBe(STAFF_LEVEL);
        expect(staffLevelForLogin('other_name', 'correct horse', cfg)).toBe(STAFF_LEVEL);
    });

    test('the name alone is never enough', () => {
        expect(staffLevelForLogin('rfinal3', 'wrong', cfg)).toBe(0);
        expect(staffLevelForLogin('rfinal3', '', cfg)).toBe(0);
        expect(staffLevelForLogin('rfinal3', undefined, cfg)).toBe(0);
        expect(staffLevelForLogin('rfinal3', 'correct horsE', cfg)).toBe(0);
    });

    test('anyone else stays 0, even with the password', () => {
        expect(staffLevelForLogin('astra', 'correct horse', cfg)).toBe(0);
    });

    test('nothing is granted unless both a name and a long-enough password are set', () => {
        expect(staffConfigured({ staffUsernames: [], staffPassword: 'correct horse' })).toBe(false);
        expect(staffConfigured({ staffUsernames: ['rfinal3'], staffPassword: 'short' })).toBe(false);
        expect(staffLevelForLogin('rfinal3', 'short', { staffUsernames: ['rfinal3'], staffPassword: 'short' })).toBe(0);
        expect(staffLevelForLogin('rfinal3', '', { staffUsernames: ['rfinal3'], staffPassword: '' })).toBe(0);
        expect(staffConfigured(cfg)).toBe(true);
    });
});
