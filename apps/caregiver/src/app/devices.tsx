/**
 * Why this exists: a nurse may look after several people, and a son may
 * have a Saathi at each parent's. Each device is one big button; the
 * current one is marked in words, not only by colour.
 */
import { router } from 'expo-router';

import { useSession } from '../state/session';
import { Body, Button, Loading, Muted, Screen, Title } from '../ui/kit';

export default function Devices() {
  const { devices, deviceId, selectDevice } = useSession();
  if (!devices) return <Loading />;
  return (
    <Screen>
      <Title>Your Saathi devices</Title>
      {devices.length === 0 ? <Body>You aren’t paired with any device yet.</Body> : null}
      {devices.map((d) => {
        const current = d.id === deviceId;
        return (
          <Button
            key={d.id}
            variant={current ? 'primary' : 'secondary'}
            label={current ? `${d.name} (showing now)` : d.name}
            accessibilityHint={current ? undefined : `Show ${d.name}`}
            onPress={() => {
              selectDevice(d.id);
              router.replace('/today');
            }}
          />
        );
      })}
      <Muted>Looking after someone else too? Pair another device.</Muted>
      <Button variant="secondary" label="Pair another device" onPress={() => router.push('/pair')} />
    </Screen>
  );
}
