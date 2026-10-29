/**
 * Why this exists: the one-time step that connects a caregiver to her
 * device. The code is typed, not scanned: eight letters and numbers with
 * no look-alikes are easy to read out over the phone ("Ma, what does it
 * show?") when the caregiver
 * isn't in the room, which is most of the time. Scanning the tablet's QR
 * is optional in the brief and left for later (expo-camera, a permission
 * prompt, and a dev build for something typing already covers).
 */
import { router, Stack } from 'expo-router';
import { useEffect, useState } from 'react';

import { claimPairing, normalizePairCode, PairingError, signOut } from '../data';
import { useSession } from '../state/session';
import { Body, Button, Card, Field, Muted, Notice, Screen, Title } from '../ui/kit';

export default function Pair() {
  const { selectDevice, devices, user } = useSession();
  const paired = (devices?.length ?? 0) > 0;
  const [code, setCode] = useState('');

  useEffect(() => {
    if (user === null) router.replace('/sign-in');
  }, [user]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const id = await claimPairing(code);
      selectDevice(id);
      router.replace('/');
    } catch (e) {
      setError(e instanceof PairingError ? e.message : `Couldn’t pair: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Stack.Screen options={{ headerBackVisible: paired, headerLeft: paired ? undefined : () => null }} />
      <Title>Connect to her Saathi</Title>
      <Body>
        Saathi’s console shows an 8-character code for adding family, like K7QM-4XHP. Type it here. A code lasts 10 minutes; if it runs out,
        the console makes a new one.
      </Body>
      <Card>
        <Field
          label="Pairing code"
          placeholder="K7QM-4XHP"
          value={code}
          onChangeText={setCode}
          autoCapitalize="characters"
          autoCorrect={false}
          autoComplete="one-time-code"
          textContentType="oneTimeCode"
          maxLength={24}
          returnKeyType="go"
          onSubmitEditing={submit}
          style={{ fontSize: 30, letterSpacing: 6 }}
        />
        {error ? <Notice>{error}</Notice> : null}
        <Button label="Pair" onPress={submit} busy={busy} disabled={!normalizePairCode(code)} />
      </Card>
      {paired ? null : (
        // Nowhere to go back to yet; this is the way out of a wrong account.
        <>
          <Muted>Signed in as {user?.email}</Muted>
          <Button variant="link" label="Sign out" onPress={() => signOut()} />
        </>
      )}
    </Screen>
  );
}
