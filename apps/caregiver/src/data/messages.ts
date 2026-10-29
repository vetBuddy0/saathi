/**
 * Why this exists: a caregiver writes a few words, Saathi says them to
 * her, and her answer comes back. The app only ever *adds* to the inbox;
 * the device sets `spokenAt` when it has said it and writes her replies.
 * So "was it spoken yet?" is read straight from the doc, never guessed.
 *
 * `from` is the caregiver's display name because it is what Saathi says
 * aloud ("A message from Ravi: ..."); `fromUid` is what the rules check.
 *
 * Length is capped: she hears this in one breath, read by a voice, and a
 * pasted email would be a minute-long monologue she can't interrupt.
 */
import {
  addDoc,
  collection,
  limit,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
} from 'firebase/firestore';

import { services } from '../firebase';
import { toMessage, toReply, type Message, type Reply, type Unsubscribe } from './types';

export const MAX_MESSAGE_LENGTH = 500;

export function validateMessage(text: string): string | null {
  const t = text.trim();
  if (!t) return 'Please write a message first.';
  if (t.length > MAX_MESSAGE_LENGTH) return `Please keep it under ${MAX_MESSAGE_LENGTH} letters — Saathi reads it aloud in one go.`;
  return null;
}

/** Put a message in her inbox. Resolves to the message id. */
export async function sendMessage(deviceId: string, text: string): Promise<string> {
  const problem = validateMessage(text);
  if (problem) throw new Error(problem);
  const { auth, db } = services();
  const user = auth.currentUser;
  if (!user) throw new Error('Please sign in first.');
  const ref = await addDoc(collection(db, 'devices', deviceId, 'inbox'), {
    from: user.displayName || user.email || 'Family',
    fromUid: user.uid,
    text: text.trim(),
    createdAt: serverTimestamp(),
  });
  return ref.id;
}

/** The latest messages to her, newest first. */
export function watchMessages(
  deviceId: string,
  cb: (messages: Message[]) => void,
  onError?: (e: Error) => void,
  max = 30,
): Unsubscribe {
  const q = query(collection(services().db, 'devices', deviceId, 'inbox'), orderBy('createdAt', 'desc'), limit(max));
  return onSnapshot(
    q,
    (snap) => cb(snap.docs.map((d) => toMessage(d.id, d.data({ serverTimestamps: 'estimate' })))),
    (e) => onError?.(e),
  );
}

/** Her replies to one message, oldest first (it reads as a conversation). */
export function watchReplies(
  deviceId: string,
  msgId: string,
  cb: (replies: Reply[]) => void,
  onError?: (e: Error) => void,
): Unsubscribe {
  const q = query(collection(services().db, 'devices', deviceId, 'inbox', msgId, 'replies'), orderBy('ts', 'asc'));
  return onSnapshot(
    q,
    (snap) => cb(snap.docs.map((d) => toReply(d.id, d.data({ serverTimestamps: 'estimate' })))),
    (e) => onError?.(e),
  );
}
