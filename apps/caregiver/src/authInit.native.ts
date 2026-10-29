/**
 * Why this exists: on iOS/Android, `getAuth` alone keeps the session in
 * memory and a caregiver would be signed out every time the app is
 * killed. Firebase's React Native build takes AsyncStorage as its
 * persistence. See `authInit.ts` for why this is a file split and not a
 * `Platform.OS` branch.
 *
 * `getReactNativePersistence` is reached through the namespace with a
 * cast: Firebase's package exports list `types` before `react-native`, so
 * tsc only ever sees the web typings, where it doesn't exist. At runtime
 * Metro resolves the React Native build, where it does. Lost: a tsconfig
 * `paths` override onto Firebase's internal dist file (breaks silently on
 * the next Firebase release that renames it).
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { FirebaseApp } from 'firebase/app';
import * as firebaseAuth from 'firebase/auth';
import type { Auth, Persistence } from 'firebase/auth';

type RNAuth = { getReactNativePersistence: (storage: typeof AsyncStorage) => Persistence };
const { getReactNativePersistence } = firebaseAuth as unknown as RNAuth;

export function createAuth(app: FirebaseApp): Auth {
  try {
    return firebaseAuth.initializeAuth(app, { persistence: getReactNativePersistence(AsyncStorage) });
  } catch {
    // Already initialised (fast refresh re-runs modules): reuse it.
    return firebaseAuth.getAuth(app);
  }
}
