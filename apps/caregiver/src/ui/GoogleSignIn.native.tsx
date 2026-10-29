/**
 * Why this exists: "Continue with Google" on iPhone and Android. It runs
 * Google's OAuth flow in the system browser through expo-auth-session,
 * gets an ID token, and passes it to `signInWithGoogleIdToken`, the same
 * data-layer step the emulator tests drive with a fake token.
 *
 * It needs the owner's OAuth client IDs (EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
 * EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID, EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
 * see the README). If this platform's ID is missing, the button says so
 * and points to email, rather than failing after a browser round-trip.
 *
 * Contested: Expo marks expo-auth-session's Google provider as
 * deprecated in favour of @react-native-google-signin/google-signin,
 * which uses Google's native SDK. That alternative lost for now because
 * it needs a dev build (it won't run in Expo Go) and per-platform native
 * config. It's the better long-term option and a contained swap: only
 * this file changes, and it still ends at `signInWithGoogleIdToken`.
 */
import * as Google from 'expo-auth-session/providers/google';
import * as WebBrowser from 'expo-web-browser';
import { useEffect, useState } from 'react';
import { Platform } from 'react-native';

import { signInWithGoogleIdToken } from '../data';
import { Button, Muted } from './kit';

WebBrowser.maybeCompleteAuthSession();

export interface GoogleSignInProps {
  onSignedIn: () => void;
  onError: (e: unknown) => void;
}

const ids = {
  iosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID || undefined,
  androidClientId: process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID || undefined,
  webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID || undefined,
};

export function GoogleSignIn(props: GoogleSignInProps) {
  const configured = Platform.OS === 'ios' ? Boolean(ids.iosClientId) : Boolean(ids.androidClientId);
  if (!configured) {
    return <Muted>Google sign-in isn’t set up in this copy of the app yet. Please use email below.</Muted>;
  }
  return <ConfiguredGoogleSignIn {...props} />;
}

function ConfiguredGoogleSignIn({ onSignedIn, onError }: GoogleSignInProps) {
  const [request, response, promptAsync] = Google.useIdTokenAuthRequest(ids);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!response) return;
    if (response.type === 'success') {
      const idToken = response.params.id_token;
      if (!idToken) {
        onError(new Error('Google didn’t send a sign-in token. Please try again.'));
        setBusy(false);
        return;
      }
      signInWithGoogleIdToken(idToken)
        .then(onSignedIn, onError)
        .finally(() => setBusy(false));
    } else {
      if (response.type === 'error') onError(response.error ?? new Error('Google sign-in failed.'));
      setBusy(false); // cancelled or dismissed: just let them try again
    }
    // The callbacks are recreated on every render of the parent; the
    // response is what this effect is about.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [response]);

  return (
    <Button
      label="Continue with Google"
      accessibilityHint="Opens Google to sign you in"
      disabled={!request}
      busy={busy}
      onPress={() => {
        setBusy(true);
        promptAsync().catch((e: unknown) => {
          setBusy(false);
          onError(e);
        });
      }}
    />
  );
}
