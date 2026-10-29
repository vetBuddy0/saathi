/**
 * Why this exists: Firebase Auth keeps the session differently per
 * platform. On web (and in Node, where the tests run) `getAuth` already
 * persists correctly — IndexedDB in a browser, memory in Node. The native
 * build needs AsyncStorage handed in explicitly, which is
 * `authInit.native.ts`; Metro picks that file on iOS/Android and this one
 * everywhere else, so no `Platform.OS` branch drags AsyncStorage into the
 * web bundle or React Native into the Node test run.
 */
import type { FirebaseApp } from 'firebase/app';
import { type Auth, getAuth } from 'firebase/auth';

export function createAuth(app: FirebaseApp): Auth {
  return getAuth(app);
}
