/**
 * Why this exists: pairing a caregiver with her device, and listing the
 * devices a caregiver is paired with.
 *
 * Pairing is a short code the tablet shows (and a QR of
 * `saathi-pair:{code}`), claimed in one batched write that marks the code
 * used and adds the caregiver to `devices/{id}.caregivers`. The batch is
 * the point: the security rules can let a caregiver add *themselves* to a
 * device only in the same write that consumes a live, unclaimed code —
 * a half-done pairing can't exist. Lost: a Cloud Function `claim` call
 * (a server round-trip and a Blaze-plan dependency for something the
 * rules can check), and the tablet approving each caregiver by tap (she
 * would be asked to approve something she can't evaluate).
 *
 * The device doc is not read before claiming: until the claim lands this
 * caregiver isn't allowed to read it, and doesn't need to.
 *
 * Under the real rules (contracts/firestore.rules), the batch must also
 * set `devices/{id}.pairingCode` to the code. A rule on the device doc
 * can't otherwise find out which pairing doc justifies the write. Either
 * write alone is denied. A caregiver can also only `get` a code that is
 * open, or that they claimed themselves. An unknown, expired or
 * already-used code therefore comes back as permission-denied rather
 * than as data, and gets one message: "that code doesn't work, ask for
 * a new one". The finer `expired`/`claimed` reasons remain for a code
 * that can be read but fails the local check, such as when the phone's
 * clock is ahead.
 */
import {
  arrayUnion,
  collection,
  doc,
  getDoc,
  getDocs,
  onSnapshot,
  query,
  where,
  writeBatch,
} from 'firebase/firestore';

import { services } from '../firebase';
import { toDate, toDevice, type Device, type Unsubscribe } from './types';

export type PairingFailure = 'invalid' | 'not_found' | 'expired' | 'claimed' | 'unavailable' | 'signed_out';

export class PairingError extends Error {
  constructor(public readonly reason: PairingFailure) {
    super(PAIRING_MESSAGES[reason]);
    this.name = 'PairingError';
  }
}

const PAIRING_MESSAGES: Record<PairingFailure, string> = {
  invalid: 'The code is the 6 numbers on Saathi’s screen.',
  not_found: 'That code isn’t right. Check the numbers on Saathi’s screen.',
  expired: 'That code has expired. Ask Saathi for a new one — they last 10 minutes.',
  claimed: 'That code has already been used. Ask Saathi for a new one.',
  unavailable: 'That code doesn’t work — it may be mistyped, used, or older than 10 minutes. Check the numbers, or ask Saathi for a new one.',
  signed_out: 'Please sign in first.',
};

/**
 * The 6-digit code from whatever was typed or scanned: "123 456",
 * "123-456" or a QR's "saathi-pair:123456". Null if it isn't one.
 */
export function normalizePairCode(input: string): string | null {
  const s = input.trim().replace(/^saathi-pair:/i, '').replace(/[\s-]/g, '');
  return /^\d{6}$/.test(s) ? s : null;
}

/** Claim a code shown on her tablet. Resolves to the device id. */
export async function claimPairing(input: string, now: Date = new Date()): Promise<string> {
  const code = normalizePairCode(input);
  if (!code) throw new PairingError('invalid');
  const { auth, db } = services();
  const uid = auth.currentUser?.uid;
  if (!uid) throw new PairingError('signed_out');

  const pairingRef = doc(db, 'pairing', code);
  const snap = await getDoc(pairingRef).catch(denied);
  if (!snap.exists()) throw new PairingError('not_found');
  const data = snap.data();
  const deviceId = typeof data.deviceId === 'string' ? data.deviceId : '';
  if (!deviceId) throw new PairingError('not_found');
  const claimedBy = typeof data.claimedBy === 'string' ? data.claimedBy : null;
  // Claiming twice from the same account (a retry after a dropped
  // connection) is a success, not an error.
  if (claimedBy === uid) return deviceId;
  if (claimedBy) throw new PairingError('claimed');
  const expiresAt = toDate(data.expiresAt);
  if (!expiresAt || expiresAt.getTime() <= now.getTime()) throw new PairingError('expired');

  const batch = writeBatch(db);
  batch.update(pairingRef, { claimedBy: uid });
  batch.update(doc(db, 'devices', deviceId), { caregivers: arrayUnion(uid), pairingCode: code });
  // Denied here means someone else claimed it, or it ran out, between
  // the read above and now.
  await batch.commit().catch(denied);
  return deviceId;
}

function denied(e: unknown): never {
  if ((e as { code?: unknown })?.code === 'permission-denied') throw new PairingError('unavailable');
  throw e;
}

function myDevicesQuery(uid: string) {
  return query(collection(services().db, 'devices'), where('caregivers', 'array-contains', uid));
}

const byName = (a: Device, b: Device): number => a.name.localeCompare(b.name) || a.id.localeCompare(b.id);

export async function listDevices(uid: string): Promise<Device[]> {
  const snap = await getDocs(myDevicesQuery(uid));
  return snap.docs.map((d) => toDevice(d.id, d.data())).sort(byName);
}

export function watchDevices(uid: string, cb: (devices: Device[]) => void, onError?: (e: Error) => void): Unsubscribe {
  return onSnapshot(
    myDevicesQuery(uid),
    (snap) => cb(snap.docs.map((d) => toDevice(d.id, d.data())).sort(byName)),
    (e) => onError?.(e),
  );
}
