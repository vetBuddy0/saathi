/**
 * Why this exists: one place decides *which* Firebase the app talks to —
 * the owner's real project, or the local emulators on this unit's ports —
 * so no screen and no data function ever holds a project id, a host or a
 * port. The data layer asks `services()`; tests and the web e2e flip the
 * same switch (`EXPO_PUBLIC_USE_EMULATORS=1`) a developer does.
 *
 * Contested: initialising at import time (the Firebase docs' shape). Lost
 * because the Jest suite sets env vars in a setup file and the device
 * actor in tests needs its own named app; a lazy getter keeps both simple.
 *
 * Expo inlines `process.env.EXPO_PUBLIC_*` only when written out in full,
 * so every variable below is read by its literal name — never through a
 * computed key or a destructure.
 */
import { type FirebaseApp, type FirebaseOptions, getApps, initializeApp } from 'firebase/app';
import { type Auth, connectAuthEmulator } from 'firebase/auth';
import { connectFirestoreEmulator, type Firestore, getFirestore } from 'firebase/firestore';
import { connectStorageEmulator, type FirebaseStorage, getStorage } from 'firebase/storage';

import { createAuth } from './authInit';

/** This unit's emulator ports (firebase.test.json). Other units use others. */
export const EMULATOR_PORTS = { auth: 9193, firestore: 8183, storage: 9293 } as const;

export interface Services {
  app: FirebaseApp;
  auth: Auth;
  db: Firestore;
  storage: FirebaseStorage;
}

export function usingEmulators(): boolean {
  return process.env.EXPO_PUBLIC_USE_EMULATORS === '1';
}

export function firebaseOptions(): FirebaseOptions {
  const projectId = process.env.EXPO_PUBLIC_FIREBASE_PROJECT_ID || (usingEmulators() ? 'demo-saathi' : '');
  return {
    // The emulators accept any key; the real project needs the real one.
    apiKey: process.env.EXPO_PUBLIC_FIREBASE_API_KEY || (usingEmulators() ? 'demo-key' : ''),
    authDomain: process.env.EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN || undefined,
    projectId,
    // Projects made since late 2024 get a *.firebasestorage.app bucket.
    storageBucket: process.env.EXPO_PUBLIC_FIREBASE_STORAGE_BUCKET || `${projectId}.firebasestorage.app`,
    messagingSenderId: process.env.EXPO_PUBLIC_FIREBASE_MESSAGING_SENDER_ID || undefined,
    appId: process.env.EXPO_PUBLIC_FIREBASE_APP_ID || (usingEmulators() ? 'demo-app' : ''),
  };
}

/**
 * Build (or reuse) the services for a named app. `name` exists for tests,
 * which run a second app as "the device"; the UI only ever uses the default.
 */
export function connect(name?: string): Services {
  const existing = getApps().find((a) => a.name === (name ?? '[DEFAULT]'));
  const app = existing ?? (name ? initializeApp(firebaseOptions(), name) : initializeApp(firebaseOptions()));
  const auth = createAuth(app);
  const db = getFirestore(app);
  const storage = getStorage(app);
  if (!existing && usingEmulators()) {
    const host = process.env.EXPO_PUBLIC_EMULATOR_HOST || '127.0.0.1';
    connectAuthEmulator(auth, `http://${host}:${EMULATOR_PORTS.auth}`, { disableWarnings: true });
    connectFirestoreEmulator(db, host, EMULATOR_PORTS.firestore);
    connectStorageEmulator(storage, host, EMULATOR_PORTS.storage);
  }
  return { app, auth, db, storage };
}

let cached: Services | null = null;

/** The app's Firebase. Every function in src/data goes through this. */
export function services(): Services {
  if (!cached) cached = connect();
  return cached;
}

/** True once a real project (or the emulators) is configured. */
export function isConfigured(): boolean {
  const o = firebaseOptions();
  return Boolean(o.apiKey && o.projectId && o.appId);
}
