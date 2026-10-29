/**
 * Why this exists: so the Cloud Functions can tell a caregiver's phone
 * "she missed her 9 o'clock" or "she replied". It asks the OS for this
 * phone's push token and keeps it in `users/{uid}.fcmTokens`.
 *
 * The *device* token (FCM on Android), not an Expo push token: the
 * owner's call is Firebase for everything, so the Functions send through
 * FCM directly and there is no second push vendor. Lost: Expo push tokens
 * (one token shape for both platforms, but every push would go through
 * Expo's service).
 *
 * iOS is a no-op for now and that is deliberate: the device token there
 * is an APNs token, which FCM cannot address until the owner uploads an
 * APNs key to Firebase and the app gains the Firebase iOS messaging SDK
 * (a dev build). Raised in DECISIONS.md rather than half-done here.
 */
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { addPushToken, removePushToken } from './users';

let registered: string | null = null;
let following: { uid: string; sub: ReturnType<typeof Notifications.addPushTokenListener> } | null = null;

/**
 * FCM rotates tokens, for example after a restore or a reinstall, or on
 * its own schedule. Swap the stored one, or the Functions keep sending
 * to a token that no longer reaches this phone.
 */
function follow(uid: string): void {
  if (following?.uid === uid) return;
  following?.sub.remove();
  const sub = Notifications.addPushTokenListener(async (t) => {
    if (typeof t.data !== 'string' || !t.data || t.data === registered) return;
    try {
      await addPushToken(uid, t.data);
      if (registered) await removePushToken(uid, registered);
      registered = t.data;
    } catch (e) {
      console.warn('push token refresh skipped:', e);
    }
  });
  following = { uid, sub };
}

export async function registerPushToken(uid: string): Promise<string | null> {
  if (Platform.OS !== 'android' || !Device.isDevice) return null;
  try {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'Saathi',
      importance: Notifications.AndroidImportance.HIGH,
    });
    let { status } = await Notifications.getPermissionsAsync();
    if (status !== 'granted') ({ status } = await Notifications.requestPermissionsAsync());
    if (status !== 'granted') return null;
    const token = await Notifications.getDevicePushTokenAsync();
    if (typeof token.data !== 'string' || !token.data) return null;
    await addPushToken(uid, token.data);
    registered = token.data;
    follow(uid);
    return token.data;
  } catch (e) {
    // No google-services.json in this build, or no Play services: the app
    // still works, the caregiver just isn't pinged. Not worth a red banner.
    console.warn('push registration skipped:', e);
    return null;
  }
}

/** Before sign-out: a phone that's signed out must stop getting her news. */
export async function unregisterPushToken(uid: string): Promise<void> {
  if (Platform.OS !== 'android' || !Device.isDevice) return;
  following?.sub.remove();
  following = null;
  try {
    // After an app restart `registered` is empty but the token is still
    // in Firestore; ask the OS again (no prompt: only if already granted).
    let token = registered;
    if (!token && (await Notifications.getPermissionsAsync()).status === 'granted') {
      const t = await Notifications.getDevicePushTokenAsync();
      token = typeof t.data === 'string' ? t.data : null;
    }
    if (token) await removePushToken(uid, token);
  } catch (e) {
    console.warn('push unregistration skipped:', e);
  } finally {
    registered = null;
  }
}
