/**
 * Why this exists: type-checks and data tests can't show whether a
 * screen actually renders, or whether a person can get from sign-in to
 * Today. This drives the exported web build (dist/, built with
 * EXPO_PUBLIC_USE_EMULATORS=1) in headless Chromium against the seeded
 * emulators. It signs in, visits each tab, adds a reminder, signs out,
 * and pairs a new account, saving a phone-sized screenshot along the
 * way. It fails if an expected piece of text never appears, so a blank
 * or crashed screen counts as a failed run rather than producing an
 * empty picture.
 *
 * Lost: Playwright. It downloads its own browsers; puppeteer-core drives
 * the Chromium this machine already has.
 *
 *   npm run seed
 *   python3 -m http.server 8790 --bind 127.0.0.1 --directory dist &
 *   npm run screenshots          # -> docs/screenshots/*.png
 *
 * The browser is found the way tests/chromium_harness.py finds it
 * (chromium, chromium-browser, google-chrome, google-chrome-stable on
 * PATH), or set CHROME=/path/to/binary.
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import puppeteer from 'puppeteer-core';

const here = dirname(fileURLToPath(import.meta.url));
const OUT = join(here, '..', 'docs', 'screenshots');
const URL = process.env.APP_URL || 'http://127.0.0.1:8790/';
const EMAIL = process.env.E2E_EMAIL || 'asha@example.com';
const PASSWORD = process.env.E2E_PASSWORD || 'saathi-demo';

function chromiumBinary() {
  if (process.env.CHROME) return process.env.CHROME;
  for (const name of ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable']) {
    try {
      return execFileSync('which', [name], { encoding: 'utf8' }).trim();
    } catch {
      // try the next one
    }
  }
  throw new Error('no chromium binary on PATH; set CHROME=');
}

async function waitForText(page, text, timeout = 15000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, text);
}

async function shot(page, name) {
  await new Promise((r) => setTimeout(r, 400)); // let fonts/layout settle
  await page.screenshot({ path: join(OUT, `${name}.png`) });
  console.log(`saved ${name}.png`);
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: chromiumBinary(),
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--window-size=430,932'],
    defaultViewport: { width: 430, height: 932, deviceScaleFactor: 1, isMobile: true, hasTouch: true },
  });
  const errors = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(URL, { waitUntil: 'networkidle0' });

    await waitForText(page, 'Continue with Google');
    await shot(page, '1-sign-in');

    // Google's popup can't be driven headless; the data test covers that
    // path (signInWithGoogleIdToken). Here: the email option.
    await page.click('[role="button"][aria-label="Use email and password instead"]');
    await waitForText(page, 'Sign in with email');
    await page.type('input[aria-label="Email"]', EMAIL);
    await page.type('input[aria-label="Password"]', PASSWORD);
    await page.click('[role="button"][aria-label="Sign in"]');

    await waitForText(page, 'Taken');
    await waitForText(page, 'Missed');
    await shot(page, '2-today');

    await page.click('[aria-label="Conversations with Saathi"]');
    await waitForText(page, 'She said');
    await shot(page, '3-conversations');

    await page.click('[aria-label="Messages to her"]');
    await waitForText(page, 'She replied');
    await waitForText(page, 'Waiting for Saathi to say it');
    await shot(page, '4-messages');

    await page.click('[aria-label="Manage reminders"]');
    await waitForText(page, 'Add a reminder');
    await waitForText(page, 'Night tablets');
    await shot(page, '5-reminders');

    // The add form, filled the way a caregiver would.
    await page.click('[role="button"][aria-label="Add a reminder"]');
    await waitForText(page, 'New reminder');
    await page.type('input[aria-label="What to remind her"]', 'Eye drops');
    await page.type('input[aria-label="Time"]', '20:30');
    await shot(page, '6-reminder-form');
    await page.click('[role="button"][aria-label="Add reminder"]');
    await page.waitForFunction(() => !document.body.innerText.includes('New reminder'), { timeout: 15000 });
    await waitForText(page, 'Eye drops');

    // Settings, sign out, and a second family member pairing with the
    // code the seed left (as the tablet's console would show it).
    await page.click('[role="button"][aria-label="Settings"]');
    await waitForText(page, 'Signed in as');
    await shot(page, '7-settings');
    await page.click('[role="button"][aria-label="Sign out"]');
    await waitForText(page, 'Continue with Google');

    await page.click('[role="button"][aria-label="Use email and password instead"]');
    await page.click('[role="button"][aria-label="New here? Create an account"]');
    await waitForText(page, 'Create an account with email');
    await page.type('input[aria-label="Your name"]', 'Meena');
    await page.type('input[aria-label="Email"]', `meena-${Date.now()}@example.com`);
    await page.type('input[aria-label="Password"]', 'saathi-demo');
    await page.click('[role="button"][aria-label="Create account"]');
    await waitForText(page, 'Connect to her Saathi');
    await page.type('input[aria-label="Pairing code"]', process.env.E2E_PAIR_CODE || '246 810');
    await shot(page, '8-pair');
    await page.click('[role="button"][aria-label="Pair"]');
    await waitForText(page, 'Taken');
    await waitForText(page, 'Ma’s Saathi');

    if (errors.length) throw new Error(`page errors:\n${errors.join('\n')}`);
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
