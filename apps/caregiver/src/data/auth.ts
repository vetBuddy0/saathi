/**
 * Why this exists: who the caregiver is.
 *
 * Google is the main way in (the owner's call, 2026-10-29). Most of the
 * family already has a Google account on their phone, so there's no
 * password to make up and later forget. Email/Password stays as the
 * second option for a nurse who won't tie a work Google account to a
 * patient's device. Firebase needs it enabled anyway, for the device's
 * own account. Lost: phone-number sign-in (SMS costs money and needs
 * reCAPTCHA on web).
 *
 * Getting a Google token is platform work: a popup on web, an OS browser
 * flow on a phone. That part lives in `google.ts` and `ui/GoogleSignIn*`.
 * This file only takes the resulting ID token to Firebase, so the step
 * the emulator tests can drive is the same step the phone runs.
 *
 * Every way in ends at `ensureProfile`, which writes `users/{uid}` if it
 * isn't there yet. That gives the name a message is signed with, and the
 * push tokens, one home the device and the Cloud Functions can read. A
 * Google account has no sign-up step, so this can't wait for one.
 */
import {
  createUserWithEmailAndPassword,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithCredential,
  signInWithEmailAndPassword,
  signOut as fbSignOut,
  updateProfile,
  type User,
} from 'firebase/auth';
import { doc, getDoc, onSnapshot, setDoc } from 'firebase/firestore';

import { services } from '../firebase';
import { unregisterPushToken } from './push';
import { toProfile, type Profile, type Unsubscribe } from './types';

export type { User };

/** Create `users/{uid}` on first sign-in. Never overwrites an existing name or tokens. */
export async function ensureProfile(user: User, name?: string): Promise<void> {
  const ref = doc(services().db, 'users', user.uid);
  const snap = await getDoc(ref);
  if (snap.exists()) return;
  const chosen = (name ?? user.displayName ?? user.email?.split('@')[0] ?? '').trim().slice(0, 100) || 'Family';
  await setDoc(ref, { name: chosen, fcmTokens: [] });
}

export async function signUp(email: string, password: string, name: string): Promise<User> {
  const trimmed = name.trim();
  if (!trimmed) throw new Error('Please enter your name, so she knows who a message is from.');
  const { auth } = services();
  const cred = await createUserWithEmailAndPassword(auth, email.trim(), password);
  await updateProfile(cred.user, { displayName: trimmed });
  await ensureProfile(cred.user, trimmed);
  return cred.user;
}

export async function signIn(email: string, password: string): Promise<User> {
  const cred = await signInWithEmailAndPassword(services().auth, email.trim(), password);
  await ensureProfile(cred.user);
  return cred.user;
}

/**
 * Sign in with a Google ID token, which a phone gets from Google's
 * OAuth flow. The web popup goes through `signInWithGooglePopup` instead.
 */
export async function signInWithGoogleIdToken(idToken: string): Promise<User> {
  const cred = await signInWithCredential(services().auth, GoogleAuthProvider.credential(idToken));
  await ensureProfile(cred.user);
  return cred.user;
}

/**
 * Removes this phone's push token first, while the user is still signed
 * in and allowed to write `users/{uid}`, so a phone that's signed out or
 * handed on stops receiving her news. This happens here rather than in
 * each screen, so no sign-out button can forget it.
 */
export async function signOut(): Promise<void> {
  const { auth } = services();
  const uid = auth.currentUser?.uid;
  if (uid) await unregisterPushToken(uid).catch(() => {});
  await fbSignOut(auth);
}

export function currentUser(): User | null {
  return services().auth.currentUser;
}

export function watchAuth(cb: (user: User | null) => void): Unsubscribe {
  return onAuthStateChanged(services().auth, cb);
}

export async function setName(name: string): Promise<void> {
  const trimmed = name.trim();
  if (!trimmed) throw new Error('Your name can’t be empty.');
  if (trimmed.length > 100) throw new Error('Please use a shorter name.');
  const { auth, db } = services();
  const user = auth.currentUser;
  if (!user) throw new Error('Not signed in.');
  await updateProfile(user, { displayName: trimmed });
  await setDoc(doc(db, 'users', user.uid), { name: trimmed }, { merge: true });
}

/** Her caregiver's own profile; `null` while `users/{uid}` doesn't exist yet. */
export function watchProfile(uid: string, cb: (p: Profile | null) => void, onError?: (e: Error) => void): Unsubscribe {
  return onSnapshot(
    doc(services().db, 'users', uid),
    (snap) => cb(snap.exists() ? toProfile(snap.data()) : null),
    (e) => onError?.(e),
  );
}

/** Plain words for the Firebase auth errors a person can actually cause. */
export function authErrorMessage(e: unknown): string {
  const code = (e as { code?: unknown })?.code;
  switch (code) {
    case 'auth/invalid-email':
      return 'That email address doesn’t look right.';
    case 'auth/missing-password':
      return 'Please enter a password.';
    case 'auth/weak-password':
      return 'Please use a password of at least 6 characters.';
    case 'auth/email-already-in-use':
      return 'There is already an account with that email. Try signing in.';
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
    case 'auth/user-not-found':
      return 'That email and password don’t match an account.';
    case 'auth/account-exists-with-different-credential':
      return 'That email already has an account with a password. Sign in with email instead.';
    case 'auth/popup-closed-by-user':
    case 'auth/cancelled-popup-request':
      return 'Google sign-in was closed before it finished. Please try again.';
    case 'auth/popup-blocked':
      return 'Your browser blocked the Google window. Please allow pop-ups for this page and try again.';
    case 'auth/too-many-requests':
      return 'Too many tries. Please wait a minute and try again.';
    case 'auth/network-request-failed':
      return 'Can’t reach the internet. Check your connection and try again.';
    default:
      return e instanceof Error && e.message ? e.message : 'Something went wrong. Please try again.';
  }
}
