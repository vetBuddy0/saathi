/**
 * Why this exists: the push-token bookkeeping on `users/{uid}.fcmTokens`,
 * kept apart from `push.native.ts` (which *gets* a token from the OS) so
 * the Firestore half runs and is tested in Node without React Native.
 *
 * arrayUnion/arrayRemove, not read-modify-write: a caregiver with a phone
 * and a tablet registers from both, and neither may drop the other's.
 */
import { arrayRemove, arrayUnion, doc, setDoc } from 'firebase/firestore';

import { services } from '../firebase';

export async function addPushToken(uid: string, token: string): Promise<void> {
  await setDoc(doc(services().db, 'users', uid), { fcmTokens: arrayUnion(token) }, { merge: true });
}

export async function removePushToken(uid: string, token: string): Promise<void> {
  await setDoc(doc(services().db, 'users', uid), { fcmTokens: arrayRemove(token) }, { merge: true });
}
