/**
 * Why this exists: her conversations with Saathi, newest first, a page at
 * a time, and the audio of a turn when the device kept one.
 *
 * Paged reads, not a live listener: a day can hold hundreds of turns and
 * a caregiver reads them like a diary, not a chat. Pull-to-refresh gets
 * the newest. Lost: `onSnapshot` over the whole collection (every new
 * turn re-sends the page to every caregiver's phone).
 *
 * Audio goes through `getDownloadURL` rather than the bytes: the player
 * streams from the URL, and Storage rules check the caregiver on the way.
 */
import {
  collection,
  getDocs,
  limit,
  orderBy,
  query,
  type QueryDocumentSnapshot,
  startAfter,
} from 'firebase/firestore';
import { getDownloadURL, ref } from 'firebase/storage';

import { services } from '../firebase';
import { toTurn, type Turn } from './types';

/** Opaque: pass back to `listTurns` to get the page after. */
export type TurnCursor = QueryDocumentSnapshot;

export interface TurnPage {
  turns: Turn[];
  next: TurnCursor | null;
}

export async function listTurns(deviceId: string, opts: { pageSize?: number; after?: TurnCursor | null } = {}): Promise<TurnPage> {
  const pageSize = opts.pageSize ?? 25;
  const base = collection(services().db, 'devices', deviceId, 'turns');
  const q = opts.after
    ? query(base, orderBy('ts', 'desc'), startAfter(opts.after), limit(pageSize))
    : query(base, orderBy('ts', 'desc'), limit(pageSize));
  const snap = await getDocs(q);
  const turns = snap.docs.map((d) => toTurn(d.id, d.data({ serverTimestamps: 'estimate' })));
  const last = snap.docs[snap.docs.length - 1];
  return { turns, next: snap.docs.length === pageSize && last ? last : null };
}

/** A playable URL for a turn's `clipPath`. */
export async function clipUrl(clipPath: string): Promise<string> {
  return getDownloadURL(ref(services().storage, clipPath));
}
