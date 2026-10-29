// Why this exists: Storage has its own rules language, so the Firestore
// tests prove nothing about her audio clips or her memory file. These run
// against the real Storage emulator, which reads devices/{id} from the
// real Firestore emulator for every decision (cross-service rules).

import { assertFails, assertSucceeds } from '@firebase/rules-unit-testing';
import { D1, D2, PRIYA, STRANGER, makeEnv, as, seedDevices } from './helpers.mjs';

let env;
const bucket = (who) => as(env, who).storage();
const bytes = () => new Uint8Array([82, 73, 70, 70, 0, 0, 0, 0]); // "RIFF...."

const AUDIO = `devices/${D1}/audio/turn-0001.wav`;
const MEMORY = `devices/${D1}/memory/2026-10-29T09-00-00Z.sqlite3`;

async function put(path) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    await ctx.storage().ref(path).put(bytes());
  });
}

before(async () => {
  env = await makeEnv();
});
after(async () => {
  await env.cleanup();
});
beforeEach(async () => {
  await env.clearFirestore();
  await env.clearStorage();
  await seedDevices(env);
});

describe('devices/{id}/audio/{turnId}.wav', () => {
  it('the device uploads, reads and deletes its audio', async () => {
    await assertSucceeds(bucket('d1').ref(AUDIO).put(bytes()));
    await assertSucceeds(bucket('d1').ref(AUDIO).getMetadata());
    await assertSucceeds(bucket('d1').ref(AUDIO).delete());
  });

  it('a caregiver reads the audio and cannot write or delete it', async () => {
    await put(AUDIO);
    await assertSucceeds(bucket(PRIYA).ref(AUDIO).getMetadata());
    await assertFails(bucket(PRIYA).ref(`devices/${D1}/audio/fake.wav`).put(bytes()));
    await assertFails(bucket(PRIYA).ref(AUDIO).delete());
  });

  it("another device can't read or write d1's audio", async () => {
    await put(AUDIO);
    await assertFails(bucket('d2').ref(AUDIO).getMetadata());
    await assertFails(bucket('d2').ref(`devices/${D1}/audio/x.wav`).put(bytes()));
    await assertSucceeds(bucket('d2').ref(`devices/${D2}/audio/x.wav`).put(bytes()));
  });

  it('a stranger and a signed-out user are denied', async () => {
    await put(AUDIO);
    await assertFails(bucket(STRANGER).ref(AUDIO).getMetadata());
    await assertFails(bucket(STRANGER).ref(`devices/${D1}/audio/x.wav`).put(bytes()));
    await assertFails(bucket('anon').ref(AUDIO).getMetadata());
  });

  it('only .wav files live in audio/', async () => {
    await assertFails(bucket('d1').ref(`devices/${D1}/audio/turn-0001.mp3`).put(bytes()));
  });
});

describe('devices/{id}/memory/{ts}.sqlite3', () => {
  it('the device uploads and reads back its memory copy', async () => {
    await assertSucceeds(bucket('d1').ref(MEMORY).put(bytes()));
    await assertSucceeds(bucket('d1').ref(MEMORY).getMetadata());
  });

  it("a caregiver can't read or write the memory copy", async () => {
    await put(MEMORY);
    await assertFails(bucket(PRIYA).ref(MEMORY).getMetadata());
    await assertFails(bucket(PRIYA).ref(`devices/${D1}/memory/x.sqlite3`).put(bytes()));
  });

  it("another device and a stranger can't touch it", async () => {
    await put(MEMORY);
    await assertFails(bucket('d2').ref(MEMORY).getMetadata());
    await assertFails(bucket('d2').ref(MEMORY).put(bytes()));
    await assertFails(bucket(STRANGER).ref(MEMORY).getMetadata());
  });

  it('only .sqlite3 files live in memory/', async () => {
    await assertFails(bucket('d1').ref(`devices/${D1}/memory/dump.json`).put(bytes()));
  });
});

describe('everything else in the bucket', () => {
  it('is denied, to the device too', async () => {
    await assertFails(bucket('d1').ref(`devices/${D1}/notes/x.txt`).put(bytes()));
    await assertFails(bucket('d1').ref('loose.wav').put(bytes()));
    await assertFails(bucket(PRIYA).ref(`devices/${D1}/x.wav`).put(bytes()));
  });

  it("a device id with no devices/{id} doc is nobody's", async () => {
    await assertFails(bucket('d1').ref('devices/ghost/audio/x.wav').put(bytes()));
  });
});
