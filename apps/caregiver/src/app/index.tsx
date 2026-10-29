/**
 * Why this exists: one gate decides where a caregiver lands. Signed out
 * goes to sign-in, no device goes to pairing, several devices with none
 * chosen goes to the picker, and otherwise Today. It's kept in one place
 * so no screen has its own idea of the order of those checks.
 */
import { Redirect } from 'expo-router';

import { signOut } from '../data';
import { isConfigured } from '../firebase';
import { useSession } from '../state/session';
import { Body, Button, Loading, Notice, Screen, Title } from '../ui/kit';

export default function Gate() {
  const { user, devices, device, devicesError, retryDevices } = useSession();
  if (!isConfigured()) {
    return (
      <Screen>
        <Title>Not set up yet</Title>
        <Body>This copy of the app has no Firebase project configured. See apps/caregiver/README.md.</Body>
      </Screen>
    );
  }
  if (user === undefined) return <Loading />;
  if (user === null) return <Redirect href="/sign-in" />;
  if (devicesError) {
    return (
      <Screen>
        <Title>Can’t load your devices</Title>
        <Notice>{devicesError}</Notice>
        <Body>You are still paired. This is usually the connection: try again in a moment.</Body>
        <Button label="Try again" onPress={retryDevices} />
        <Button variant="link" label="Sign out" onPress={() => signOut()} />
      </Screen>
    );
  }
  if (devices === undefined) return <Loading label="Finding your devices" />;
  if (devices.length === 0) return <Redirect href="/pair" />;
  if (!device) return <Redirect href="/devices" />;
  return <Redirect href="/today" />;
}
