// Why this exists: the Firestore rules are the only thing between a
// stranger and her conversations, and every other unit (device client,
// caregiver app, Cloud Functions) codes against them. Each allow and each
// deny in firestore.rules has a test here, run against the real Firestore
// emulator -- this suite is the end-to-end test for contracts/.

import assert from 'node:assert/strict';
import { assertFails, assertSucceeds } from '@firebase/rules-unit-testing';
import {
  D1, D2, D1_UID, D2_UID, PRIYA, RAVI, NEW, STRANGER,
  FieldValue, Timestamp,
  makeEnv, as, sample, seed, seedDevices, peek, inMinutes,
} from './helpers.mjs';

let env;
const db = (who) => as(env, who).firestore();

before(async () => {
  env = await makeEnv();
});
after(async () => {
  await env.cleanup();
});
beforeEach(async () => {
  await env.clearFirestore();
  await seedDevices(env);
});

// ---------------------------------------------------------------------------
describe('devices/{id}', () => {
  const fresh = () => ({
    name: "Amma's Saathi",
    createdAt: Timestamp.now(),
    caregivers: [],
    deviceUid: D1_UID,
  });

  beforeEach(async () => {
    await env.clearFirestore();
  });

  it('the device creates its own doc, empty of caregivers', async () => {
    await assertSucceeds(db('d1').doc(`devices/${D1}`).set(fresh()));
  });

  it("a device can't create the doc of a device id that isn't its email", async () => {
    await assertFails(db('d1').doc(`devices/${D2}`).set({ ...fresh(), deviceUid: D1_UID }));
  });

  it("a device id must be long and random-looking, even with a matching email", async () => {
    for (const id of ['d1', 'short-id', 'has.dot.in.it.123456', 'x'.repeat(65)]) {
      const ctx = env.authenticatedContext(D1_UID, { email: `device-${id}@saathi.invalid` });
      await assertFails(ctx.firestore().doc(`devices/${id}`).set(fresh()), id);
    }
  });

  it("a device can't create its doc with caregivers already on it", async () => {
    await assertFails(db('d1').doc(`devices/${D1}`).set({ ...fresh(), caregivers: [STRANGER] }));
  });

  it("a device can't create its doc naming another uid as the device", async () => {
    await assertFails(db('d1').doc(`devices/${D1}`).set({ ...fresh(), deviceUid: STRANGER }));
  });

  it("a stranger can't create a device doc", async () => {
    await assertFails(db(STRANGER).doc(`devices/${D1}`).set({ ...fresh(), deviceUid: STRANGER }));
    await assertFails(db('anon').doc(`devices/${D1}`).set(fresh()));
  });

  it('the device doc rejects fields outside the contract', async () => {
    await assertFails(db('d1').doc(`devices/${D1}`).set({ ...fresh(), extra: 1 }));
  });
});

describe('devices/{id}: reading', () => {
  it('a caregiver reads the device, and lists their devices by array-contains', async () => {
    await assertSucceeds(db(PRIYA).doc(`devices/${D1}`).get());
    const snap = await assertSucceeds(
      db(PRIYA).collection('devices').where('caregivers', 'array-contains', PRIYA).get(),
    );
    assert.deepEqual(snap.docs.map((d) => d.id), [D1]);
  });

  it("a caregiver can't list devices without the array-contains filter", async () => {
    await assertFails(db(PRIYA).collection('devices').get());
  });

  it("a caregiver can't read a device they're not on", async () => {
    await assertFails(db(PRIYA).doc(`devices/${D2}`).get());
  });

  it('the device reads its own doc', async () => {
    await assertSucceeds(db('d1').doc(`devices/${D1}`).get());
  });

  it("a device can't read another device", async () => {
    await assertFails(db('d2').doc(`devices/${D1}`).get());
  });

  it('a stranger and a signed-out user are denied', async () => {
    await assertFails(db(STRANGER).doc(`devices/${D1}`).get());
    await assertFails(db('anon').doc(`devices/${D1}`).get());
    await assertFails(db(STRANGER).collection('devices').get());
  });
});

describe('devices/{id}: changing who is on it', () => {
  const ref = (who) => db(who).doc(`devices/${D1}`);

  it('a caregiver renames the device', async () => {
    await assertSucceeds(ref(PRIYA).update({ name: 'Amma' }));
  });

  it('a caregiver removes themselves', async () => {
    await assertSucceeds(ref(PRIYA).update({ caregivers: FieldValue.arrayRemove(PRIYA) }));
    assert.deepEqual((await peek(env, `devices/${D1}`)).caregivers, [RAVI]);
  });

  it("a caregiver can't remove another caregiver", async () => {
    await assertFails(ref(PRIYA).update({ caregivers: FieldValue.arrayRemove(RAVI) }));
    await assertFails(ref(PRIYA).update({ caregivers: [] }));
    await assertFails(ref(PRIYA).update({ caregivers: [PRIYA] }));
  });

  it("a caregiver can't pad the list with duplicates", async () => {
    await assertFails(ref(PRIYA).update({ caregivers: [PRIYA, RAVI, RAVI] }));
    await assertFails(ref(PRIYA).update({ caregivers: [RAVI, RAVI] }));
  });

  it("a caregiver can't add someone (joining is pairing)", async () => {
    await assertFails(ref(PRIYA).update({ caregivers: FieldValue.arrayUnion(STRANGER) }));
  });

  it("a caregiver can't change deviceUid or createdAt", async () => {
    await assertFails(ref(PRIYA).update({ deviceUid: PRIYA }));
    await assertFails(ref(PRIYA).update({ createdAt: Timestamp.now() }));
  });

  it("a caregiver can't delete the device", async () => {
    await assertFails(ref(PRIYA).delete());
  });

  it('the device removes a caregiver', async () => {
    await assertSucceeds(ref('d1').update({ caregivers: FieldValue.arrayRemove(RAVI) }));
  });

  it("the device can't hand itself to another uid", async () => {
    await assertFails(ref('d1').update({ deviceUid: STRANGER }));
  });

  it("another device and a stranger can't write it", async () => {
    await assertFails(ref('d2').update({ name: 'mine' }));
    await assertFails(ref(STRANGER).update({ name: 'mine' }));
    await assertFails(ref(STRANGER).update({ caregivers: FieldValue.arrayUnion(STRANGER) }));
  });
});

// ---------------------------------------------------------------------------
for (const kind of ['turns', 'events']) {
  const name = kind === 'turns' ? 'turn' : 'event';
  describe(`devices/{id}/${kind}`, () => {
    const path = (dev = D1) => `devices/${dev}/${kind}/x1`;

    it(`the device writes the ${name} sample`, async () => {
      await assertSucceeds(db('d1').doc(path()).set(sample(name)));
    });

    it(`a caregiver reads ${kind} and can't write them`, async () => {
      await seed(env, { [path()]: sample(name) });
      await assertSucceeds(db(PRIYA).doc(path()).get());
      await assertSucceeds(db(PRIYA).collection(`devices/${D1}/${kind}`).get());
      await assertFails(db(PRIYA).doc(`devices/${D1}/${kind}/x2`).set(sample(name)));
      await assertFails(db(PRIYA).doc(path()).update(kind === 'turns' ? { said: 'changed' } : { text: 'changed' }));
      await assertFails(db(PRIYA).doc(path()).delete());
    });

    it(`another device can't read or write d1's ${kind}`, async () => {
      await seed(env, { [path()]: sample(name) });
      await assertFails(db('d2').doc(path()).get());
      await assertFails(db('d2').doc(`devices/${D1}/${kind}/x2`).set(sample(name)));
      await assertSucceeds(db('d2').doc(path(D2)).set(sample(name, { clipPath: undefined })));
    });

    it(`a stranger can't read or write ${kind}`, async () => {
      await seed(env, { [path()]: sample(name) });
      await assertFails(db(STRANGER).doc(path()).get());
      await assertFails(db(STRANGER).doc(`devices/${D1}/${kind}/x2`).set(sample(name)));
      await assertFails(db('anon').doc(path()).get());
    });

    it(`a ${name} outside the contract is refused`, async () => {
      await assertFails(db('d1').doc(path()).set(sample(name, { extra: true })));
      await assertFails(db('d1').doc(path()).set(sample(name, { ts: '2026-10-29T09:30:05Z' })));
    });
  });
}

describe('turns and events: field rules', () => {
  it('an event kind outside the list is refused', async () => {
    await assertFails(db('d1').doc(`devices/${D1}/events/e`).set(sample('event', { kind: 'snoozed' })));
  });

  it("a turn's clipPath must be this device's audio", async () => {
    await assertSucceeds(db('d1').doc(`devices/${D1}/turns/t`).set(sample('turn', { clipPath: undefined })));
    await assertFails(
      db('d1').doc(`devices/${D1}/turns/t`).set(sample('turn', { clipPath: `devices/${D2}/audio/t.wav` })),
    );
  });
});

// ---------------------------------------------------------------------------
describe('devices/{id}/inbox', () => {
  const msgPath = `devices/${D1}/inbox/m1`;
  const fromPriya = () =>
    sample('message', { fromUid: PRIYA, spokenAt: undefined, createdAt: Timestamp.now() });

  it('a caregiver sends a message signed with their own uid', async () => {
    await assertSucceeds(db(PRIYA).doc(msgPath).set(fromPriya()));
  });

  it("a caregiver can't sign a message as someone else", async () => {
    await assertFails(db(PRIYA).doc(msgPath).set({ ...fromPriya(), fromUid: RAVI }));
  });

  it("a caregiver can't send a message already marked spoken", async () => {
    await assertFails(db(PRIYA).doc(msgPath).set({ ...fromPriya(), spokenAt: Timestamp.now() }));
  });

  it('a message has to be text she can be told', async () => {
    await assertFails(db(PRIYA).doc(msgPath).set({ ...fromPriya(), text: '' }));
    await assertFails(db(PRIYA).doc(msgPath).set({ ...fromPriya(), text: 'x'.repeat(1001) }));
  });

  it("a stranger, the device and another device can't send one", async () => {
    await assertFails(db(STRANGER).doc(msgPath).set({ ...fromPriya(), fromUid: STRANGER }));
    await assertFails(db('d1').doc(msgPath).set({ ...fromPriya(), fromUid: D1_UID }));
    await assertFails(db('d2').doc(msgPath).set({ ...fromPriya(), fromUid: D2_UID }));
  });

  it('the device reads the inbox and writes spokenAt', async () => {
    await seed(env, { [msgPath]: fromPriya() });
    await assertSucceeds(db('d1').collection(`devices/${D1}/inbox`).get());
    await assertSucceeds(db('d1').doc(msgPath).update({ spokenAt: Timestamp.now() }));
  });

  it("the device can't change what a message says", async () => {
    await seed(env, { [msgPath]: fromPriya() });
    await assertFails(db('d1').doc(msgPath).update({ text: 'something else' }));
  });

  it("a caregiver can't write spokenAt or edit a sent message", async () => {
    await seed(env, { [msgPath]: fromPriya() });
    await assertFails(db(PRIYA).doc(msgPath).update({ spokenAt: Timestamp.now() }));
    await assertFails(db(PRIYA).doc(msgPath).update({ text: 'edited' }));
  });

  it('the sender takes back an unheard message; nobody else can', async () => {
    await seed(env, { [msgPath]: fromPriya() });
    await assertFails(db(RAVI).doc(msgPath).delete());
    await assertSucceeds(db(PRIYA).doc(msgPath).delete());
  });

  it("a message she has heard can't be taken back by the sender", async () => {
    await seed(env, { [msgPath]: { ...fromPriya(), spokenAt: Timestamp.now() } });
    await assertFails(db(PRIYA).doc(msgPath).delete());
  });

  it("another device and a stranger can't read the inbox", async () => {
    await seed(env, { [msgPath]: fromPriya() });
    await assertFails(db('d2').doc(msgPath).get());
    await assertFails(db(STRANGER).doc(msgPath).get());
  });
});

describe('devices/{id}/inbox/{msg}/replies', () => {
  const replyPath = `devices/${D1}/inbox/m1/replies/r1`;

  it('the device writes the reply sample; caregivers read it', async () => {
    await assertSucceeds(db('d1').doc(replyPath).set(sample('reply')));
    await assertSucceeds(db(PRIYA).doc(replyPath).get());
    await assertSucceeds(db(RAVI).collection(`devices/${D1}/inbox/m1/replies`).get());
  });

  it("a caregiver can't write a reply", async () => {
    await assertFails(db(PRIYA).doc(replyPath).set(sample('reply')));
  });

  it("another device and a stranger can't read or write replies", async () => {
    await seed(env, { [replyPath]: sample('reply') });
    await assertFails(db('d2').doc(replyPath).get());
    await assertFails(db('d2').doc(replyPath).set(sample('reply')));
    await assertFails(db(STRANGER).doc(replyPath).get());
  });

  it('a reply outside the contract is refused', async () => {
    await assertFails(db('d1').doc(replyPath).set(sample('reply', { from: 'Amma' })));
  });
});

// ---------------------------------------------------------------------------
describe('devices/{id}/reminders', () => {
  const rPath = `devices/${D1}/reminders/r1`;
  const byPriya = (o = {}) => sample('reminder', { createdBy: PRIYA, updatedAt: Timestamp.now(), ...o });

  it('a caregiver creates the reminder sample as themselves', async () => {
    await assertSucceeds(db(PRIYA).doc(rPath).set(byPriya()));
  });

  it('date is optional', async () => {
    await assertSucceeds(db(PRIYA).doc(rPath).set(byPriya({ date: undefined, repeat: 'none' })));
  });

  it("a caregiver can't create one in someone else's name", async () => {
    await assertFails(db(PRIYA).doc(rPath).set(byPriya({ createdBy: RAVI })));
  });

  it('time, date and repeat must be in the contract', async () => {
    for (const bad of [
      { time: '25:00' }, { time: '8:00' }, { date: '2026-13-01' }, { date: '30/10/2026' },
      { repeat: 'monthly' }, { active: 'yes' }, { text: '' }, { extra: 1 },
    ]) {
      await assertFails(db(PRIYA).doc(rPath).set(byPriya(bad)), JSON.stringify(bad));
    }
  });

  it('another caregiver edits it; nobody changes who created it', async () => {
    await seed(env, { [rPath]: byPriya() });
    await assertSucceeds(db(RAVI).doc(rPath).update({ time: '09:30', updatedAt: Timestamp.now() }));
    await assertFails(db(RAVI).doc(rPath).update({ createdBy: RAVI }));
  });

  it('a caregiver deletes one', async () => {
    await seed(env, { [rPath]: byPriya() });
    await assertSucceeds(db(RAVI).doc(rPath).delete());
  });

  it('the device reads reminders and may only switch one off', async () => {
    await seed(env, { [rPath]: byPriya() });
    await assertSucceeds(db('d1').collection(`devices/${D1}/reminders`).get());
    await assertSucceeds(db('d1').doc(rPath).update({ active: false, updatedAt: Timestamp.now() }));
    await assertFails(db('d1').doc(rPath).update({ text: 'changed' }));
    await assertFails(db('d1').doc(`devices/${D1}/reminders/r2`).set(byPriya({ createdBy: D1_UID })));
    await assertFails(db('d1').doc(rPath).delete());
  });

  it("another device and a stranger can't read or write reminders", async () => {
    await seed(env, { [rPath]: byPriya() });
    await assertFails(db('d2').doc(rPath).get());
    await assertFails(db(STRANGER).doc(rPath).get());
    await assertFails(db(STRANGER).doc(`devices/${D1}/reminders/r2`).set(byPriya({ createdBy: STRANGER })));
    await assertFails(db(STRANGER).doc(rPath).delete());
  });
});

describe('collections outside the contract', () => {
  it('are denied to everyone, the device included', async () => {
    await assertFails(db('d1').doc(`devices/${D1}/notes/n1`).set({ text: 'x' }));
    await assertFails(db(PRIYA).doc(`devices/${D1}/notes/n1`).get());
    await assertFails(db(PRIYA).doc('elsewhere/x').set({ a: 1 }));
  });
});

// ---------------------------------------------------------------------------
describe('pairing/{code}', () => {
  const CODE = 'K7QM4XHP';
  const open = (o = {}) => ({ deviceId: D1, expiresAt: inMinutes(10), ...o });

  // The claim, exactly as the app does it: one batch.
  const claim = (who, code = CODE, deviceId = D1, claimedBy = who) => {
    const f = db(who);
    const b = f.batch();
    b.update(f.doc(`pairing/${code}`), { claimedBy });
    b.update(f.doc(`devices/${deviceId}`), {
      caregivers: FieldValue.arrayUnion(who),
      pairingCode: code,
    });
    return b.commit();
  };

  describe('opening a code', () => {
    it('the device opens an 8-character code for itself, 10 minutes out', async () => {
      await assertSucceeds(db('d1').doc(`pairing/${CODE}`).set(open()));
    });

    it('the pairing sample (unclaimed) passes as a fresh code', async () => {
      await assertSucceeds(
        db('d1').doc(`pairing/${CODE}`).set(sample('pairing', { claimedBy: undefined, expiresAt: inMinutes(9) })),
      );
    });

    it("a device can't open a code for another device", async () => {
      await assertFails(db('d2').doc(`pairing/${CODE}`).set(open()));
    });

    it("a code can't live longer than 10 minutes (plus a minute of clock)", async () => {
      await assertFails(db('d1').doc(`pairing/${CODE}`).set(open({ expiresAt: inMinutes(30) })));
      await assertFails(db('d1').doc(`pairing/${CODE}`).set(open({ expiresAt: inMinutes(-1) })));
    });

    // Was "exactly 6 digits": too few to survive guessing (owner, 2026-10-29).
    it('a code is exactly 8 unambiguous characters and opens unclaimed', async () => {
      await assertFails(db('d1').doc('pairing/482913').set(open())); // the old 6 digits
      await assertFails(db('d1').doc('pairing/K7QM4XH').set(open())); // 7
      await assertFails(db('d1').doc('pairing/K7QM4XHPP').set(open())); // 9
      await assertFails(db('d1').doc('pairing/k7qm4xhp').set(open())); // lower case
      await assertFails(db('d1').doc('pairing/K7QM-4XH').set(open())); // a dash
      await assertFails(db('d1').doc('pairing/K7QM4XH0').set(open())); // 0 looks like O
      await assertFails(db('d1').doc('pairing/K7QM4XHI').set(open())); // I looks like 1
      await assertFails(db('d1').doc(`pairing/${CODE}`).set(open({ claimedBy: PRIYA })));
    });

    it("a device can't take over a code another device holds", async () => {
      await seed(env, { [`pairing/${CODE}`]: open({ deviceId: D2, expiresAt: inMinutes(-5) }) });
      await assertFails(db('d1').doc(`pairing/${CODE}`).set(open()));
    });

    it("a caregiver or stranger can't open a code", async () => {
      await assertFails(db(PRIYA).doc(`pairing/${CODE}`).set(open()));
      await assertFails(db(STRANGER).doc(`pairing/${CODE}`).set(open()));
    });
  });

  describe('reading codes', () => {
    it('the device lists and deletes its own codes', async () => {
      await seed(env, { [`pairing/${CODE}`]: open(), 'pairing/22222222': open({ deviceId: D2 }) });
      const snap = await assertSucceeds(db('d1').collection('pairing').where('deviceId', '==', D1).get());
      assert.deepEqual(snap.docs.map((d) => d.id), [CODE]);
      await assertSucceeds(db('d1').doc(`pairing/${CODE}`).delete());
    });

    it("a device can't read or delete another device's code", async () => {
      await seed(env, { [`pairing/${CODE}`]: open({ claimedBy: PRIYA }) });
      await assertFails(db('d2').doc(`pairing/${CODE}`).get());
      await assertFails(db('d2').doc(`pairing/${CODE}`).delete());
      await assertFails(db('d2').collection('pairing').where('deviceId', '==', D1).get());
    });

    it('someone holding an open code may fetch it, but never list codes', async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await assertSucceeds(db(NEW).doc(`pairing/${CODE}`).get());
      await assertFails(db(NEW).collection('pairing').get());
      await assertFails(db(NEW).collection('pairing').where('deviceId', '==', D1).get());
      await assertFails(db('anon').doc(`pairing/${CODE}`).get());
    });

    it("an expired code can't be fetched", async () => {
      await seed(env, { [`pairing/${CODE}`]: open({ expiresAt: inMinutes(-1) }) });
      await assertFails(db(NEW).doc(`pairing/${CODE}`).get());
    });
  });

  describe('claiming a code', () => {
    it('a signed-in caregiver claims an open code and joins the device', async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await assertSucceeds(claim(NEW));
      const device = await peek(env, `devices/${D1}`);
      assert.deepEqual(device.caregivers, [PRIYA, RAVI, NEW]);
      assert.equal(device.pairingCode, CODE);
      assert.equal((await peek(env, `pairing/${CODE}`)).claimedBy, NEW);
      // ...and from then on is a caregiver like any other.
      await assertSucceeds(db(NEW).doc(`devices/${D1}`).get());
      await assertSucceeds(db(NEW).doc(`pairing/${CODE}`).get());
    });

    it('the claimed device doc and pairing doc still match their schemas', async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await claim(NEW);
      const device = await peek(env, `devices/${D1}`);
      assert.deepEqual(Object.keys(device).sort(), Object.keys(sample('device')).sort());
      assert.deepEqual(
        Object.keys(await peek(env, `pairing/${CODE}`)).sort(),
        Object.keys(sample('pairing')).sort(),
      );
    });

    it('an expired code is refused', async () => {
      await seed(env, { [`pairing/${CODE}`]: open({ expiresAt: inMinutes(-1) }) });
      await assertFails(claim(NEW));
      assert.deepEqual((await peek(env, `devices/${D1}`)).caregivers, [PRIYA, RAVI]);
    });

    it('a code works once', async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await assertSucceeds(claim(NEW));
      await assertFails(claim(STRANGER));
    });

    it("a code for one device can't join another", async () => {
      await seed(env, { [`pairing/${CODE}`]: open({ deviceId: D2 }) });
      await assertFails(claim(NEW, CODE, D1));
    });

    it("a claim can't be made in someone else's name", async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await assertFails(claim(NEW, CODE, D1, STRANGER));
    });

    it('joining without claiming a code in the same batch is refused', async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await assertFails(
        db(NEW).doc(`devices/${D1}`).update({ caregivers: FieldValue.arrayUnion(NEW), pairingCode: CODE }),
      );
    });

    it('claiming a code without joining in the same batch is refused', async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await assertFails(db(NEW).doc(`pairing/${CODE}`).update({ claimedBy: NEW }));
    });

    it("a removed caregiver can't replay their old code to rejoin", async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      await assertSucceeds(claim(NEW));
      await assertSucceeds(db('d1').doc(`devices/${D1}`).update({ caregivers: FieldValue.arrayRemove(NEW) }));
      await assertFails(
        db(NEW).doc(`devices/${D1}`).update({ caregivers: FieldValue.arrayUnion(NEW), pairingCode: CODE }),
      );
      await assertFails(claim(NEW));
    });

    it("the claim can't also change anything else on the device", async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      const f = db(NEW);
      const b = f.batch();
      b.update(f.doc(`pairing/${CODE}`), { claimedBy: NEW });
      b.update(f.doc(`devices/${D1}`), {
        caregivers: [NEW], // drops Priya and Ravi
        pairingCode: CODE,
      });
      await assertFails(b.commit());
    });

    it("a claimer can't move the expiry", async () => {
      await seed(env, { [`pairing/${CODE}`]: open() });
      const f = db(NEW);
      const b = f.batch();
      b.update(f.doc(`pairing/${CODE}`), { claimedBy: NEW, expiresAt: inMinutes(60) });
      b.update(f.doc(`devices/${D1}`), { caregivers: FieldValue.arrayUnion(NEW), pairingCode: CODE });
      await assertFails(b.commit());
    });
  });
});

// ---------------------------------------------------------------------------
describe('users/{uid}', () => {
  it('a user writes and reads their own profile (the user sample)', async () => {
    await assertSucceeds(db(PRIYA).doc(`users/${PRIYA}`).set(sample('user')));
    await assertSucceeds(db(PRIYA).doc(`users/${PRIYA}`).update({ fcmTokens: FieldValue.arrayUnion('t2') }));
    await assertSucceeds(db(PRIYA).doc(`users/${PRIYA}`).get());
  });

  it("nobody else reads or writes it, not a fellow caregiver, not the device", async () => {
    await seed(env, { [`users/${PRIYA}`]: sample('user') });
    for (const who of [RAVI, STRANGER, 'd1', 'anon']) {
      await assertFails(db(who).doc(`users/${PRIYA}`).get(), who);
      await assertFails(db(who).doc(`users/${PRIYA}`).set(sample('user')), who);
    }
  });

  it('a profile outside the contract is refused', async () => {
    await assertFails(db(PRIYA).doc(`users/${PRIYA}`).set(sample('user', { admin: true })));
    await assertFails(db(PRIYA).doc(`users/${PRIYA}`).set(sample('user', { fcmTokens: 'one' })));
  });
});

// ---------------------------------------------------------------------------
describe('every sample passes the rules, written by its rightful writer', () => {
  it('device (as the device updating its own doc)', async () => {
    await assertSucceeds(
      db('d1').doc(`devices/${D1}`).set(sample('device', { createdAt: (await peek(env, `devices/${D1}`)).createdAt })),
    );
  });
  it('turn, event, reply (as the device)', async () => {
    await assertSucceeds(db('d1').doc(`devices/${D1}/turns/s`).set(sample('turn')));
    await assertSucceeds(db('d1').doc(`devices/${D1}/events/s`).set(sample('event')));
    await assertSucceeds(db('d1').doc(`devices/${D1}/inbox/m/replies/s`).set(sample('reply')));
  });
  it('message (as its sender, then spokenAt by the device)', async () => {
    const msg = sample('message');
    const { spokenAt, ...unsent } = msg;
    await assertSucceeds(db(msg.fromUid).doc(`devices/${D1}/inbox/s`).set(unsent));
    await assertSucceeds(db('d1').doc(`devices/${D1}/inbox/s`).update({ spokenAt }));
  });
  it('reminder (as its creator)', async () => {
    const r = sample('reminder');
    await assertSucceeds(db(r.createdBy).doc(`devices/${D1}/reminders/s`).set(r));
  });
});
