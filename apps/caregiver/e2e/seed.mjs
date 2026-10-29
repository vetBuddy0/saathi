/**
 * Why this exists: an empty app proves nothing, and the screenshots
 * should show a day that looks like hers. One script makes that day
 * the same way every time: two caregivers, a paired device, reminders
 * that were taken, missed and are still to come, a few conversation
 * turns (one with a clip), and messages with and without a reply. It
 * also opens one live pairing code. It's used by the screenshot run, and
 * is handy for trying the web build by hand.
 *
 *   firebase emulators:start --config firebase.test.json \
 *     --only auth,firestore,storage --project demo-saathi
 *   npm run seed     # prints the sign-in it made
 *
 * Each document is written by whoever owns it in real life: the device
 * writes its own docs, Asha and Ravi write theirs. That way the seed
 * passes the real rules in rules/ and shows what each party may write.
 */
import { deleteApp, initializeApp } from 'firebase/app';
import { connectAuthEmulator, createUserWithEmailAndPassword, getAuth, updateProfile } from 'firebase/auth';
import {
  addDoc,
  collection,
  connectFirestoreEmulator,
  doc,
  getFirestore,
  serverTimestamp,
  setDoc,
  terminate,
  Timestamp,
  updateDoc,
} from 'firebase/firestore';
import { connectStorageEmulator, getStorage, ref, uploadBytes } from 'firebase/storage';

export const CAREGIVER = { email: 'asha@example.com', password: 'saathi-demo', name: 'Asha' };
// The rules want 16-64 unguessable characters; this one is a demo.
export const DEVICE_ID = 'ma-saathi-demo-tablet';
export const PAIR_CODE = '246810';
const HOST = process.env.EMULATOR_HOST || '127.0.0.1';
// The same bucket the app falls back to when none is configured.
const config = { apiKey: 'demo-key', projectId: 'demo-saathi', appId: 'demo-app', storageBucket: 'demo-saathi.firebasestorage.app' };

function connect(name) {
  const app = initializeApp(config, name);
  const auth = getAuth(app);
  const db = getFirestore(app);
  const storage = getStorage(app);
  connectAuthEmulator(auth, `http://${HOST}:9193`, { disableWarnings: true });
  connectFirestoreEmulator(db, HOST, 8183);
  connectStorageEmulator(storage, HOST, 9293);
  return { app, auth, db, storage };
}

async function wipe() {
  // Fresh every run: same emails, same ids, no "email already in use".
  await fetch(`http://${HOST}:9193/emulator/v1/projects/demo-saathi/accounts`, { method: 'DELETE' });
  await fetch(`http://${HOST}:8183/emulator/v1/projects/demo-saathi/databases/(default)/documents`, { method: 'DELETE' });
}

/** A short, quiet sine tone as a valid 16-bit mono WAV. */
function toneWav(seconds = 1, hz = 330, rate = 8000) {
  const n = seconds * rate;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const s = (o, str) => [...str].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  s(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); s(8, 'WAVE'); s(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  s(36, 'data'); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.sin((2 * Math.PI * hz * i) / rate) * 4000, true);
  return new Uint8Array(buf);
}

export async function seed() {
  await wipe();
  const device = connect('seed-device');
  const carer = connect('seed-caregiver');
  const ravi = connect('seed-ravi');

  // The device account's email is bound to its id by the rules.
  const dev = await createUserWithEmailAndPassword(device.auth, `device-${DEVICE_ID}@saathi.invalid`, 'device-secret');
  const me = await createUserWithEmailAndPassword(carer.auth, CAREGIVER.email, CAREGIVER.password);
  await updateProfile(me.user, { displayName: CAREGIVER.name });
  await setDoc(doc(carer.db, 'users', me.user.uid), { name: CAREGIVER.name, fcmTokens: [] });
  const raviUser = await createUserWithEmailAndPassword(ravi.auth, 'ravi@example.com', 'saathi-demo');
  await updateProfile(raviUser.user, { displayName: 'Ravi' });
  await setDoc(doc(ravi.db, 'users', raviUser.user.uid), { name: 'Ravi', fcmTokens: [] });

  // A device starts with nobody on its list. The device itself may then
  // set it, which here stands in for Asha and Ravi having paired earlier.
  await setDoc(doc(device.db, 'devices', DEVICE_ID), {
    name: 'Ma’s Saathi',
    createdAt: serverTimestamp(),
    caregivers: [],
    deviceUid: dev.user.uid,
  });
  await updateDoc(doc(device.db, 'devices', DEVICE_ID), { caregivers: [me.user.uid, raviUser.user.uid] });

  const today = new Date();
  const at = (h, m) => Timestamp.fromDate(new Date(today.getFullYear(), today.getMonth(), today.getDate(), h, m));
  const rem = (text, time, repeat, extra = {}) =>
    addDoc(collection(carer.db, 'devices', DEVICE_ID, 'reminders'), {
      text, time, repeat, active: true, createdBy: me.user.uid, updatedAt: Timestamp.now(), ...extra,
    });
  const ev = (rid, kind, h, m, text) =>
    addDoc(collection(device.db, 'devices', DEVICE_ID, 'events'), { ts: at(h, m), kind, reminderId: rid.id, text });

  const morning = await rem('Morning tablets', '07:30', 'daily');
  const sugar = await rem('Check blood sugar', '09:00', 'daily');
  const lunch = await rem('Lunch tablets', '13:00', 'daily');
  await rem('Evening walk', '18:00', 'weekdays');
  await rem('Night tablets', '21:00', 'daily');
  await rem('Call Meena', '11:00', 'none', { active: false, date: '2026-10-20' });
  await ev(morning, 'fired', 7, 30, 'Morning tablets');
  await ev(morning, 'acked', 7, 34, 'Morning tablets');
  await ev(sugar, 'fired', 9, 0, 'Check blood sugar');
  await ev(sugar, 'missed', 9, 20, 'Check blood sugar');
  await ev(lunch, 'fired', 13, 0, 'Lunch tablets');

  const turns = [
    [8, 2, 'Good morning Saathi, did it rain last night?', 'Good morning! Yes, a little rain after midnight. It is cool and clear now.', 'en'],
    [10, 15, 'Mera chashma kahan rakha tha maine?', 'Kal aapne kaha tha ki chashma TV ke paas wali mez par rakha hai.', 'hi', true],
    [11, 40, 'Play some old Kishore Kumar songs', 'Playing “Kishore Kumar classics” on the screen now.', 'en'],
    [12, 5, 'Is Asha coming this weekend?', 'Asha said she is coming on Sunday. Shall I remind you on Saturday evening?', 'en'],
  ];
  const wav = toneWav();
  for (const [h, m, heard, said, language, clip] of turns) {
    const tref = doc(collection(device.db, 'devices', DEVICE_ID, 'turns'));
    const clipPath = clip ? `devices/${DEVICE_ID}/audio/${tref.id}.wav` : undefined;
    if (clipPath) await uploadBytes(ref(device.storage, clipPath), wav, { contentType: 'audio/wav' });
    await setDoc(tref, { ts: at(h, m), heard, said, language, ...(clipPath ? { clipPath } : {}) });
  }

  const m1 = await addDoc(collection(carer.db, 'devices', DEVICE_ID, 'inbox'), {
    from: CAREGIVER.name, fromUid: me.user.uid, text: 'Coming on Sunday with the children, Ma. Make your kheer!', createdAt: at(9, 45),
  });
  // Only the device marks a message spoken and writes her replies.
  await setDoc(doc(device.db, 'devices', DEVICE_ID, 'inbox', m1.id), { spokenAt: at(10, 2) }, { merge: true });
  await addDoc(collection(device.db, 'devices', DEVICE_ID, 'inbox', m1.id, 'replies'), {
    ts: at(10, 3), text: 'Yes yes, come early. I will make kheer and puri.',
  });
  // Ravi signs his own message: the rules require fromUid == the sender.
  await addDoc(collection(ravi.db, 'devices', DEVICE_ID, 'inbox'), {
    from: 'Ravi', fromUid: raviUser.user.uid, text: 'Did you take the sugar test today?', createdAt: at(12, 30),
  });

  // A live code, as the tablet's console would show it, for a second
  // caregiver to claim (the screenshot run pairs a fresh account with it).
  await setDoc(doc(device.db, 'pairing', PAIR_CODE), {
    deviceId: DEVICE_ID,
    expiresAt: Timestamp.fromMillis(Date.now() + 10 * 60 * 1000),
  });

  for (const s of [device, carer, ravi]) {
    await terminate(s.db);
    await deleteApp(s.app);
  }
  return CAREGIVER;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  seed().then(
    (c) => {
      console.log(`seeded: sign in as ${c.email} / ${c.password}`);
      process.exit(0);
    },
    (e) => {
      console.error(e);
      process.exit(1);
    },
  );
}
