/**
 * Why this exists: the data layer is only right if the real backend
 * accepts what it writes and returns what it reads. So it runs against
 * the Firebase emulators under the project's real security rules
 * (rules/, copied verbatim from contracts/), not against mocks or open
 * rules.
 *
 * There are two actors, as in life. The caregiver goes through src/data,
 * which is what the app does. "The device" is a second Firebase app
 * signed in as its own `device-{id}@saathi.invalid` account. It writes
 * the documents the tablet writes (device doc, pairing code, events,
 * replies, spokenAt, turns, audio), and it uses the SDK directly on
 * purpose: its side is the contract, not this app's code. A third
 * account plays another family member where a test needs one.
 *
 * Run with `npm run test:emulators` (firebase emulators:exec).
 */
import { deleteApp } from 'firebase/app';
import { createUserWithEmailAndPassword, signOut as fbSignOut } from 'firebase/auth';
import {
  addDoc,
  arrayUnion,
  collection,
  doc,
  getDoc,
  serverTimestamp,
  setDoc,
  terminate,
  Timestamp,
  updateDoc,
  writeBatch,
} from 'firebase/firestore';
import { ref as storageRef, uploadBytes } from 'firebase/storage';

import {
  authErrorMessage,
  setName,
  signIn,
  signInWithGoogleIdToken,
  signOut,
  signUp,
} from '../src/data/auth';
import { claimPairing, listDevices, PairingError, watchDevices } from '../src/data/devices';
import { sendMessage, watchMessages, watchReplies } from '../src/data/messages';
import { cancelReminder, ReminderValidationError, saveReminder } from '../src/data/reminders';
import { type TodayItem, watchToday } from '../src/data/today';
import { clipUrl, listTurns } from '../src/data/turns';
import type { Device, Message, Reply, Unsubscribe } from '../src/data/types';
import { addPushToken, removePushToken } from '../src/data/users';
import { connect, services, type Services } from '../src/firebase';

const run = Math.random().toString(36).slice(2, 8);
// The rules require an unguessable 16-64 character id, bound to the
// device account's email.
const DEVICE_ID = `ma-${run}-tablet-0001`;
const PASSWORD = 'correct-horse-9';
const caregiverEmail = `ravi-${run}@example.com`;

let device: Services;
let other: Services;
let caregiverUid = '';

/** Resolve with the first value a watcher emits that satisfies `ok`. */
function until<T>(watch: (cb: (v: T) => void) => Unsubscribe, ok: (v: T) => boolean, ms = 10000): Promise<T> {
  return new Promise((resolve, reject) => {
    let stop: Unsubscribe = () => {};
    let done = false;
    const timer = setTimeout(() => {
      done = true;
      stop();
      reject(new Error('timed out waiting for watcher'));
    }, ms);
    stop = watch((v) => {
      if (done || !ok(v)) return;
      done = true;
      clearTimeout(timer);
      // stop() may not be assigned yet if the first snapshot is synchronous.
      setTimeout(() => stop(), 0);
      resolve(v);
    });
  });
}

/** A watcher error is a failed test, not a silent hang. */
const fail = (e: Error) => {
  throw e;
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let codeSeq = 0;
const freshCode = () => {
  // 8 random characters from the pairing alphabet: 30^8 codes, so runs
  // and calls don't collide.
  const A = '23456789ABCDEFGHJKMNPQRSTVWXYZ';
  codeSeq += 1;
  return Array.from({ length: 8 }, () => A[Math.floor(Math.random() * A.length)]).join('');
};

/** The tablet's console opening a pairing code. */
async function openCode(ttlMs = 10 * 60 * 1000): Promise<string> {
  const code = freshCode();
  await setDoc(doc(device.db, 'pairing', code), { deviceId: DEVICE_ID, expiresAt: Timestamp.fromMillis(Date.now() + ttlMs) });
  return code;
}

const myProfile = async () => (await getDoc(doc(services().db, 'users', caregiverUid))).data();

beforeAll(async () => {
  if (!process.env.FIRESTORE_EMULATOR_HOST) {
    throw new Error('These tests need the emulators. Run: npm run test:emulators');
  }
  device = connect('device');
  other = connect('other-caregiver');
  const cred = await createUserWithEmailAndPassword(device.auth, `device-${DEVICE_ID}@saathi.invalid`, PASSWORD);
  // What the tablet does once, when it is set up: nobody on the list yet.
  await setDoc(doc(device.db, 'devices', DEVICE_ID), {
    name: 'Ma’s Saathi',
    createdAt: serverTimestamp(),
    caregivers: [],
    deviceUid: cred.user.uid,
  });
});

afterAll(async () => {
  for (const s of [services(), device, other]) {
    await fbSignOut(s.auth).catch(() => {});
    await terminate(s.db);
    await deleteApp(s.app);
  }
});

describe('accounts', () => {
  test('sign up writes the profile; sign in works; bad password is plain words', async () => {
    const user = await signUp(caregiverEmail, PASSWORD, ' Ravi ');
    caregiverUid = user.uid;
    expect(user.displayName).toBe('Ravi');
    expect(await myProfile()).toEqual({ name: 'Ravi', fcmTokens: [] });

    await signOut();
    expect(services().auth.currentUser).toBeNull();
    const err = await signIn(caregiverEmail, 'wrong-password').catch((e: unknown) => e);
    expect(authErrorMessage(err)).toMatch(/don’t match/);
    const again = await signIn(caregiverEmail, PASSWORD);
    expect(again.uid).toBe(caregiverUid);
    expect(await myProfile()).toEqual({ name: 'Ravi', fcmTokens: [] }); // sign-in never resets it
  });

  test('setName updates the profile without touching tokens', async () => {
    await addPushToken(caregiverUid, 'tok-a');
    await setName('Ravi K');
    expect(await myProfile()).toEqual({ name: 'Ravi K', fcmTokens: ['tok-a'] });
  });

  test('push tokens are a set: added once, removed alone', async () => {
    await addPushToken(caregiverUid, 'tok-b');
    await addPushToken(caregiverUid, 'tok-b');
    expect((await myProfile())?.fcmTokens).toEqual(['tok-a', 'tok-b']);
    await removePushToken(caregiverUid, 'tok-a');
    expect((await myProfile())?.fcmTokens).toEqual(['tok-b']);
  });
});

describe('pairing', () => {
  test('claiming a live code adds the caregiver to the device, in one batch with pairingCode', async () => {
    const code = await openCode();
    const seen = until<Device[]>((cb) => watchDevices(caregiverUid, cb, fail), (ds) => ds.some((d) => d.id === DEVICE_ID));

    // Typed with a space, the way it's read off the screen.
    expect(await claimPairing(`${code.slice(0, 3)} ${code.slice(3)}`)).toBe(DEVICE_ID);

    const dev = (await getDoc(doc(device.db, 'devices', DEVICE_ID))).data();
    expect(dev?.caregivers).toEqual([caregiverUid]);
    expect(dev?.pairingCode).toBe(code);
    expect((await getDoc(doc(device.db, 'pairing', code))).data()?.claimedBy).toBe(caregiverUid);
    await seen;

    const devices = await listDevices(caregiverUid);
    expect(devices.map((d) => [d.id, d.name])).toEqual([[DEVICE_ID, 'Ma’s Saathi']]);

    // A retry after a dropped connection is a success, and adds nobody twice.
    expect(await claimPairing(`saathi-pair:${code}`)).toBe(DEVICE_ID);
    expect((await getDoc(doc(device.db, 'devices', DEVICE_ID))).data()?.caregivers).toEqual([caregiverUid]);
  });

  test('expired, claimed-by-someone-else, unknown and malformed codes are refused in plain words', async () => {
    const reason = (p: Promise<unknown>) => p.then(() => 'ok', (e: unknown) => (e instanceof PairingError ? e.reason : String(e)));

    const shortLived = await openCode(1000);
    // Well past expiry: the emulator's request.time and this process's
    // clock can disagree by around a second.
    await sleep(4000);
    // The rules hide an expired code entirely; if the clocks disagree,
    // the app's own check refuses it instead. Either way, nothing is joined.
    expect(await reason(claimPairing(shortLived))).toMatch(/^(unavailable|expired)$/);
    expect((await getDoc(doc(device.db, 'devices', DEVICE_ID))).data()?.caregivers).toEqual([caregiverUid]);

    // Another family member gets there first, through the same batch the app sends.
    const taken = await openCode();
    const otherUser = await createUserWithEmailAndPassword(other.auth, `meena-${run}@example.com`, PASSWORD);
    const batch = writeBatch(other.db);
    batch.update(doc(other.db, 'pairing', taken), { claimedBy: otherUser.user.uid });
    batch.update(doc(other.db, 'devices', DEVICE_ID), { caregivers: arrayUnion(otherUser.user.uid), pairingCode: taken });
    await batch.commit();
    expect(await reason(claimPairing(taken))).toBe('unavailable');

    expect(await reason(claimPairing('ZZZZ2222'))).toBe('unavailable'); // never opened
    expect(await reason(claimPairing('12345'))).toBe('invalid');
    expect(new PairingError('unavailable').message).toMatch(/ask Saathi for a new one/);
  });
});

describe('messages', () => {
  test('a message reaches the inbox; the device speaks it and her reply comes back', async () => {
    const msgId = await sendMessage(DEVICE_ID, '  Coming on Sunday, Ma  ');
    const inbox = (await getDoc(doc(device.db, 'devices', DEVICE_ID, 'inbox', msgId))).data();
    expect(inbox).toEqual(expect.objectContaining({ from: 'Ravi K', fromUid: caregiverUid, text: 'Coming on Sunday, Ma' }));
    expect(inbox?.createdAt).toBeInstanceOf(Timestamp);
    expect(inbox?.spokenAt).toBeUndefined();

    const spoken = until<Message[]>(
      (cb) => watchMessages(DEVICE_ID, cb, fail),
      (ms) => ms.some((m) => m.id === msgId && m.spokenAt !== null),
    );
    const replied = until<Reply[]>((cb) => watchReplies(DEVICE_ID, msgId, cb, fail), (rs) => rs.length === 1);

    // The device: says it, then writes what she answered.
    await updateDoc(doc(device.db, 'devices', DEVICE_ID, 'inbox', msgId), { spokenAt: serverTimestamp() });
    await addDoc(collection(device.db, 'devices', DEVICE_ID, 'inbox', msgId, 'replies'), {
      ts: serverTimestamp(),
      text: 'Bring the grandchildren',
    });

    const msgs = await spoken;
    expect(msgs[0]?.id).toBe(msgId);
    expect(msgs[0]?.spokenAt).toBeInstanceOf(Date);
    const replies = await replied;
    expect(replies[0]?.text).toBe('Bring the grandchildren');
    expect(replies[0]?.ts).toBeInstanceOf(Date);
  });

  test('an empty message is refused before anything is written', async () => {
    await expect(sendMessage(DEVICE_ID, '   ')).rejects.toThrow(/write a message/);
  });
});

describe('reminders', () => {
  const remDoc = (rid: string) => doc(device.db, 'devices', DEVICE_ID, 'reminders', rid);

  test('save, edit (createdBy kept), cancel', async () => {
    const id = await saveReminder(DEVICE_ID, { text: 'Blood pressure tablet', time: '8:00', repeat: 'daily' });
    let r = (await getDoc(remDoc(id))).data();
    expect(r).toEqual(
      expect.objectContaining({ text: 'Blood pressure tablet', time: '08:00', repeat: 'daily', active: true, createdBy: caregiverUid }),
    );
    expect(r?.updatedAt).toBeInstanceOf(Timestamp);
    expect('date' in (r ?? {})).toBe(false); // optional means absent, not null

    await saveReminder(DEVICE_ID, { text: 'BP tablet', time: '08:30', repeat: 'weekly', date: '2026-11-02' }, id);
    r = (await getDoc(remDoc(id))).data();
    expect(r).toEqual(
      expect.objectContaining({ text: 'BP tablet', time: '08:30', repeat: 'weekly', date: '2026-11-02', createdBy: caregiverUid }),
    );

    await saveReminder(DEVICE_ID, { text: 'BP tablet', time: '08:30', repeat: 'daily', date: '' }, id);
    expect('date' in ((await getDoc(remDoc(id))).data() ?? {})).toBe(false);

    const before = (await getDoc(remDoc(id))).data()?.updatedAt as Timestamp;
    await sleep(20);
    await cancelReminder(DEVICE_ID, id);
    r = (await getDoc(remDoc(id))).data();
    expect(r?.active).toBe(false);
    expect(r?.text).toBe('BP tablet'); // cancelled, not deleted: the device sees the flag
    expect((r?.updatedAt as Timestamp).toMillis()).toBeGreaterThan(before.toMillis());
  });

  test('another caregiver may edit it; who created it never changes', async () => {
    const id = await saveReminder(DEVICE_ID, { text: 'Walk', time: '17:00', repeat: 'daily' });
    // Meena (paired in the pairing tests) edits it with her own client.
    await setDoc(
      doc(other.db, 'devices', DEVICE_ID, 'reminders', id),
      { text: 'Evening walk', updatedAt: serverTimestamp() },
      { merge: true },
    );
    expect((await getDoc(remDoc(id))).data()).toEqual(expect.objectContaining({ text: 'Evening walk', createdBy: caregiverUid }));
  });

  test('an invalid reminder is never written', async () => {
    await expect(saveReminder(DEVICE_ID, { text: '', time: '9', repeat: 'daily' })).rejects.toBeInstanceOf(ReminderValidationError);
  });
});

describe('today', () => {
  test('combines her reminders with what the device recorded today', async () => {
    const tablets = await saveReminder(DEVICE_ID, { text: 'Morning tablets', time: '07:00', repeat: 'daily' });
    const water = await saveReminder(DEVICE_ID, { text: 'Drink water', time: '11:00', repeat: 'daily' });
    const walk = await saveReminder(DEVICE_ID, { text: 'Night walk', time: '23:59', repeat: 'daily' });
    const ev = (rid: string, kind: string, secondsAgo: number) =>
      addDoc(collection(device.db, 'devices', DEVICE_ID, 'events'), {
        ts: Timestamp.fromMillis(Date.now() - secondsAgo * 1000),
        kind,
        reminderId: rid,
        text: 'x',
      });
    // Yesterday's tick must not count for today.
    await ev(walk, 'acked', 36 * 3600);
    // Seconds ago, so still "today" unless run in the first seconds after midnight.
    await ev(tablets, 'fired', 3);
    await ev(tablets, 'acked', 2);
    await ev(water, 'fired', 2);
    await ev(water, 'missed', 1);

    const items = await until<TodayItem[]>(
      (cb) => watchToday(DEVICE_ID, cb, fail),
      (is) => is.find((i) => i.reminderId === water)?.status === 'missed' && is.find((i) => i.reminderId === tablets)?.status === 'taken',
    );
    const status = Object.fromEntries(items.map((i) => [i.reminderId, i.status]));
    expect(status[tablets]).toBe('taken');
    expect(status[water]).toBe('missed');
    expect(status[walk]).toBe('pending');
  });
});

describe('conversations', () => {
  test('turns newest first, paged, with a playable clip', async () => {
    const base = Date.now() - 60 * 60 * 1000;
    for (let i = 0; i < 5; i++) {
      const turnId = `t${i}`;
      const clipPath = i === 4 ? `devices/${DEVICE_ID}/audio/${turnId}.wav` : undefined;
      await setDoc(doc(device.db, 'devices', DEVICE_ID, 'turns', turnId), {
        ts: Timestamp.fromMillis(base + i * 60000),
        heard: `heard ${i}`,
        said: `said ${i}`,
        language: 'hi',
        ...(clipPath ? { clipPath } : {}),
      });
    }
    await uploadBytes(storageRef(device.storage, `devices/${DEVICE_ID}/audio/t4.wav`), new Uint8Array([82, 73, 70, 70]), {
      contentType: 'audio/wav',
    });

    const first = await listTurns(DEVICE_ID, { pageSize: 3 });
    expect(first.turns.map((t) => t.id)).toEqual(['t4', 't3', 't2']);
    expect(first.next).not.toBeNull();
    const second = await listTurns(DEVICE_ID, { pageSize: 3, after: first.next });
    expect(second.turns.map((t) => t.id)).toEqual(['t1', 't0']);
    expect(second.next).toBeNull();

    const clip = first.turns[0]?.clipPath;
    expect(clip).toBe(`devices/${DEVICE_ID}/audio/t4.wav`);
    const url = await clipUrl(clip!);
    const res = await fetch(url);
    expect(res.ok).toBe(true);
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([82, 73, 70, 70]));
  });
});

describe('google sign-in', () => {
  // The Auth emulator accepts an unsigned JSON "ID token"; the phone hands
  // a real one to the same function.
  const fakeIdToken = (sub: string, email: string) => JSON.stringify({ sub, email, email_verified: true });

  test('first Google sign-in creates the profile; later ones keep a changed name', async () => {
    await signOut();
    const email = `asha-${run}@example.com`;
    const user = await signInWithGoogleIdToken(fakeIdToken(`g-${run}`, email));
    expect(user.email).toBe(email);
    expect(user.providerData.map((p) => p.providerId)).toContain('google.com');
    const profileOf = async () => (await getDoc(doc(services().db, 'users', user.uid))).data();
    expect(await profileOf()).toEqual({ name: `asha-${run}`, fcmTokens: [] });

    await setName('Asha');
    await signOut();
    const again = await signInWithGoogleIdToken(fakeIdToken(`g-${run}`, email));
    expect(again.uid).toBe(user.uid);
    expect(await profileOf()).toEqual({ name: 'Asha', fcmTokens: [] });

    // A Google caregiver pairs exactly like an email one.
    const code = await openCode();
    expect(await claimPairing(code)).toBe(DEVICE_ID);
    expect((await listDevices(user.uid)).map((d) => d.id)).toEqual([DEVICE_ID]);
  });
});
