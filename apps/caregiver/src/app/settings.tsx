/**
 * Why this exists: change the name she hears ("A message from …"), pair
 * another device, and sign out. (Signing out removes this phone's push
 * token. That's done in `signOut` itself, so every sign-out does it.)
 */
import { router } from 'expo-router';
import { useEffect, useState } from 'react';

import { authErrorMessage, setName, signOut } from '../data';
import { useSession } from '../state/session';
import { Body, Button, Card, Field, Heading, Muted, Notice, Screen } from '../ui/kit';

export default function Settings() {
  const { user, devices } = useSession();
  const [name, setNameInput] = useState(user?.displayName ?? '');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (user === null) router.replace('/sign-in');
  }, [user]);

  const save = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await setName(name);
      setSaved(true);
    } catch (e) {
      setError(authErrorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Screen>
      <Card>
        <Heading>Your name</Heading>
        <Field
          label="Name Saathi says"
          hint="She hears “A message from …” before what you write."
          value={name}
          onChangeText={(t) => {
            setNameInput(t);
            setSaved(false);
          }}
          autoComplete="name"
        />
        {error ? <Notice>{error}</Notice> : null}
        {saved ? <Notice kind="info">Saved.</Notice> : null}
        <Button label="Save name" onPress={save} busy={saving} />
      </Card>
      <Card>
        <Heading>Devices</Heading>
        <Body>
          {devices?.length === 1 ? 'You are paired with 1 device.' : `You are paired with ${devices?.length ?? 0} devices.`}
        </Body>
        <Button variant="secondary" label="Pair another device" onPress={() => router.push('/pair')} />
      </Card>
      <Card>
        <Heading>Account</Heading>
        <Muted>Signed in as {user?.email}</Muted>
        <Button variant="danger" label="Sign out" onPress={() => signOut()} />
      </Card>
    </Screen>
  );
}
