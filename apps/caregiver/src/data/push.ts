/**
 * Why this exists: the web build (and the Node test run) has no push.
 * Callers can call these on any platform; here, off iOS/Android, they do
 * nothing. The real ones are in `push.native.ts`, which Metro picks on a
 * phone. A file split, not a `Platform.OS` check, so expo-notifications
 * never lands in the web bundle.
 *
 * Lost: Web Push via the FCM web SDK. It needs a service worker and a
 * VAPID key, and a browser tab is not where her son should learn she
 * missed her tablets.
 */
export async function registerPushToken(_uid: string): Promise<string | null> {
  return null;
}

export async function unregisterPushToken(_uid: string): Promise<void> {
  // Nothing was registered on this platform.
}
