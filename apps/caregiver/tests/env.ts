/**
 * Why this exists: no test may ever reach a real Firebase project. Jest
 * loads this before any module, so `src/firebase.ts` reads emulator
 * settings through the same `EXPO_PUBLIC_*` switch a developer uses.
 * There's no separate test-only code path to drift from the app's.
 */
process.env.EXPO_PUBLIC_USE_EMULATORS = '1';
process.env.EXPO_PUBLIC_FIREBASE_PROJECT_ID = 'demo-saathi';
process.env.EXPO_PUBLIC_FIREBASE_API_KEY = 'demo-key';
process.env.EXPO_PUBLIC_FIREBASE_APP_ID = 'demo-app';
process.env.EXPO_PUBLIC_FIREBASE_STORAGE_BUCKET = 'demo-saathi.firebasestorage.app';
process.env.EXPO_PUBLIC_EMULATOR_HOST = '127.0.0.1';
