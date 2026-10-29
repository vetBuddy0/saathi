/**
 * Why this exists: on web, Firebase runs Google sign-in itself, in a
 * popup. It needs no OAuth client of ours, only the authorised domain
 * in the Firebase console. Firebase's React Native build has no popup at
 * all, so this lives in its own file. `google.native.ts` stands in for it
 * on a phone, where `ui/GoogleSignIn.native.tsx` gets an ID token through
 * expo-auth-session and calls `signInWithGoogleIdToken` instead.
 *
 * Lost: `signInWithRedirect`. It reloads the whole page, and in Safari and
 * Chrome it now depends on third-party storage, which they block unless
 * the app is served from the auth domain.
 */
import { GoogleAuthProvider, signInWithPopup, type User } from 'firebase/auth';

import { services } from '../firebase';
import { ensureProfile } from './auth';

export async function signInWithGooglePopup(): Promise<User> {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: 'select_account' });
  const cred = await signInWithPopup(services().auth, provider);
  await ensureProfile(cred.user);
  return cred.user;
}
