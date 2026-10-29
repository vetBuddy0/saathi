// Why this exists: both rules suites need the same cast (one device, its
// caregiver, a second device, a stranger) and the same bridge from the JSON
// samples to Firestore documents. Keeping that here means the samples the
// Python test validates against the schemas are byte-for-byte the documents
// these tests push through the rules.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { initializeTestEnvironment } from '@firebase/rules-unit-testing';
import firebase from 'firebase/compat/app';
import 'firebase/compat/firestore';

// The test contexts hand out compat instances, so the tests use the compat
// API throughout rather than mixing it with the modular one.
export const { Timestamp, FieldValue } = firebase.firestore;

// Every denied write is expected here; the SDK would log each one.
firebase.firestore.setLogLevel('silent');

const here = dirname(fileURLToPath(import.meta.url));
export const root = join(here, '..');

export const PROJECT_ID = 'demo-saathi';

// The cast. d1 is "her" tablet; d2 is someone else's tablet.
// Device ids are random, 16-64 of [A-Za-z0-9_-] (the rules insist).
export const D1 = 'k3Jd9QmZp2LxV7tWc4Hn';
export const D2 = 'Q8vRt2NwYb6Ls1Fa0Ue5';
export const D1_UID = 'uid-device-d1';
export const D2_UID = 'uid-device-d2';
export const PRIYA = 'uid-priya'; // caregiver of d1
export const RAVI = 'uid-ravi'; // second caregiver of d1
export const NEW = 'uid-new'; // signed in, about to pair
export const STRANGER = 'uid-stranger';

export const deviceEmail = (id) => `device-${id}@saathi.invalid`;

export async function makeEnv() {
  return initializeTestEnvironment({
    projectId: PROJECT_ID,
    firestore: { rules: readFileSync(join(root, 'firestore.rules'), 'utf8') },
    storage: { rules: readFileSync(join(root, 'storage.rules'), 'utf8') },
  });
}

/** An authed context; devices carry the device email in their token. */
export function as(env, who) {
  switch (who) {
    case 'd1':
      return env.authenticatedContext(D1_UID, { email: deviceEmail(D1) });
    case 'd2':
      return env.authenticatedContext(D2_UID, { email: deviceEmail(D2) });
    case 'anon':
      return env.unauthenticatedContext();
    default:
      return env.authenticatedContext(who, { email: `${who}@example.com` });
  }
}

const schemaOf = (name) =>
  JSON.parse(readFileSync(join(root, 'schemas', `${name}.schema.json`), 'utf8'));

/**
 * contracts/samples/<name>.json as a Firestore document: every property the
 * schema marks `format: date-time` (an ISO string in JSON) becomes a
 * Firestore Timestamp, which is what is stored. `overrides` win; a value of
 * undefined drops the key.
 */
export function sample(name, overrides = {}) {
  const raw = JSON.parse(readFileSync(join(root, 'samples', `${name}.json`), 'utf8'));
  const props = schemaOf(name).properties;
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    out[k] = props[k]?.format === 'date-time' ? Timestamp.fromDate(new Date(v)) : v;
  }
  for (const [k, v] of Object.entries(overrides)) {
    if (v === undefined) delete out[k];
    else out[k] = v;
  }
  return out;
}

export const inMinutes = (m) => Timestamp.fromMillis(Date.now() + m * 60_000);

/** d1 with Priya and Ravi, d2 with nobody: the starting world for most tests. */
export async function seedDevices(env, { d1Caregivers = [PRIYA, RAVI] } = {}) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    await db.doc(`devices/${D1}`).set({
      name: "Amma's Saathi",
      createdAt: Timestamp.now(),
      caregivers: d1Caregivers,
      deviceUid: D1_UID,
    });
    await db.doc(`devices/${D2}`).set({
      name: 'Other tablet',
      createdAt: Timestamp.now(),
      caregivers: [],
      deviceUid: D2_UID,
    });
  });
}

/** Write docs with the rules off: {path: data}. */
export async function seed(env, docs) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    for (const [path, data] of Object.entries(docs)) await db.doc(path).set(data);
  });
}

/** Read a doc with the rules off, to check what a write really left behind. */
export async function peek(env, path) {
  let data;
  await env.withSecurityRulesDisabled(async (ctx) => {
    const snap = await ctx.firestore().doc(path).get();
    data = snap.exists ? snap.data() : undefined;
  });
  return data;
}
