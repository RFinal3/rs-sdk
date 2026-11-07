#!/usr/bin/env bun
/**
 * Camera mode (PLAYER_CLIENT=headless-browser): Astra's game client as a real browser page,
 * headless, inside the player container. It replaces the lite client, so the canvas exists and
 * the bridge's GET /screenshot (sdk.sendScreenshot -> canvas.toDataURL) returns a real picture.
 *
 *   bun astra-world/bridge/camera.ts <botname>      (reads bots/<botname>/bot.env)
 *
 * The password is read from bot.env here and only ever passed to the page URL inside this
 * process: it is not on any command line and never printed. Exits non-zero if the page dies or
 * leaves the game, so player-entry.sh exits and compose restarts the container (as with lite).
 *
 * Env: CHROMIUM_PATH (default /usr/bin/chromium), CAMERA_FPS (default 5),
 *      CAMERA_GL (default swiftshader; "none" to omit the GL flags).
 */
import puppeteer from 'puppeteer';

const name = process.argv[2];
if (!name) { console.error('usage: camera.ts <botname>'); process.exit(2); }

const envText = await Bun.file(`/opt/rs/bots/${name}/bot.env`).text();
const env = Object.fromEntries(envText.split('\n').filter(l => l.includes('='))
    .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()]));
if (!env.PASSWORD) { console.error('[camera] bot.env has no PASSWORD'); process.exit(2); }

const fps = Number(process.env.CAMERA_FPS || 5);
const gl = process.env.CAMERA_GL || 'swiftshader';
const url = `http://localhost:8888/bot?bot=${encodeURIComponent(name)}&password=${encodeURIComponent(env.PASSWORD)}&fps=${fps}`;

const browser = await puppeteer.launch({
    executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true,
    args: [
        '--no-sandbox', '--disable-dev-shm-usage', '--mute-audio', '--disable-extensions',
        '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
        '--disable-backgrounding-occluded-windows', '--no-proxy-server', '--window-size=765,600',
        ...(gl === 'none' ? [] : [`--use-gl=${gl}`, '--enable-unsafe-swiftshader']),
    ],
    protocolTimeout: 120_000,
});
const page = await browser.newPage();
await page.setViewport({ width: 765, height: 600 });
const die = (why: string) => {
    console.error(`[camera] ${why}; exiting so the container restarts`);
    browser.close().catch(() => {}).finally(() => process.exit(1));
};
page.on('error', e => die(`page crashed: ${e.message}`));
browser.on('disconnected', () => die('browser disconnected'));
page.on('pageerror', e => console.error(`[camera] page error: ${e.message}`.replace(env.PASSWORD, '<redacted>')));

console.log(`[camera] opening the bot page for ${name} (fps ${fps}, gl ${gl})`);
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120_000 });

const deadline = Date.now() + 180_000;
while (!(await page.evaluate(() => Boolean((window as any).gameClient?.ingame)).catch(() => false))) {
    if (Date.now() > deadline) die('not in game after 180 s');
    await Bun.sleep(500);
}
console.log(`[camera] ${name} is in game in headless Chromium`);

// Stay up; leave if the client drops out of the game for more than 60 s.
let outSince: number | null = null;
for (;;) {
    await Bun.sleep(5_000);
    const ingame = await page.evaluate(() => Boolean((window as any).gameClient?.ingame)).catch(() => false);
    if (ingame) outSince = null;
    else if (outSince === null) outSince = Date.now();
    else if (Date.now() - outSince > 60_000) die('out of game for 60 s');
}
