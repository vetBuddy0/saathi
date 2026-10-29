/**
 * Why this exists: Firebase's React Native build has no `signInWithPopup`.
 * This file keeps `src/data`'s exports the same on every platform, so
 * nothing imports a function that would be undefined on a phone. On a
 * phone, Google sign-in goes through `ui/GoogleSignIn.native.tsx` and
 * `signInWithGoogleIdToken`.
 */
import type { User } from 'firebase/auth';

export async function signInWithGooglePopup(): Promise<User> {
  throw new Error('On a phone, Google sign-in goes through the native flow (GoogleSignIn.native.tsx).');
}
