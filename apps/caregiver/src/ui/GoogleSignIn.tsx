/**
 * Why this exists: the "Continue with Google" button on web. Firebase's
 * popup does the whole flow, so the web build needs no OAuth client IDs.
 * The phone version is `GoogleSignIn.native.tsx`. It's a file split so
 * expo-auth-session never enters the web bundle, and Firebase's popup
 * (which doesn't exist on React Native) never enters the phone's.
 */
import { useState } from 'react';

import { signInWithGooglePopup } from '../data';
import { Button } from './kit';

export interface GoogleSignInProps {
  onSignedIn: () => void;
  onError: (e: unknown) => void;
}

export function GoogleSignIn({ onSignedIn, onError }: GoogleSignInProps) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      label="Continue with Google"
      accessibilityHint="Signs you in with your Google account"
      busy={busy}
      onPress={async () => {
        setBusy(true);
        try {
          await signInWithGooglePopup();
          onSignedIn();
        } catch (e) {
          onError(e);
        } finally {
          setBusy(false);
        }
      }}
    />
  );
}
