/**
 * Why this exists: nearly every screen needs three facts: who is signed
 * in, which devices they're paired with, and which one they're looking
 * at. They're held once, live, so each screen doesn't subscribe again.
 * The chosen device is remembered across launches, so a nurse with three
 * patients lands where she left off.
 *
 * `undefined` means "still finding out", and `null` or `[]` means "known
 * to be none". The router gate in app/index.tsx relies on the
 * difference, or a signed-in caregiver would see the sign-in screen
 * flash on every start. A failed device list is an error with a retry,
 * never `[]`: "you have no devices" would send a paired caregiver to ask
 * her for a new code.
 *
 * The push token is registered only once `users/{uid}` exists. The rules
 * require a name on that doc, and the first sign-in creates it a moment
 * after auth fires. Registering any earlier either gets denied or races
 * the doc's creation.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from 'react';

import { type Device, registerPushToken, type User, watchAuth, watchDevices, watchProfile } from '../data';

const DEVICE_KEY = 'saathi.selectedDevice';

interface Session {
  user: User | null | undefined;
  devices: Device[] | undefined;
  deviceId: string | null;
  device: Device | null;
  selectDevice: (id: string) => void;
  devicesError: string | null;
  retryDevices: () => void;
}

const Ctx = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [devices, setDevices] = useState<Device[] | undefined>(undefined);
  const [chosen, setChosen] = useState<string | null>(null);
  const [devicesError, setDevicesError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => watchAuth(setUser), []);

  useEffect(() => {
    AsyncStorage.getItem(DEVICE_KEY)
      .then((id) => id && setChosen((c) => c ?? id))
      .catch(() => {});
  }, []);

  const uid = user?.uid;

  useEffect(() => {
    setDevices(undefined);
    setDevicesError(null);
    if (!uid) return;
    return watchDevices(uid, setDevices, (e) => setDevicesError(e.message));
  }, [uid, attempt]);

  useEffect(() => {
    if (!uid) return;
    let registered = false;
    return watchProfile(
      uid,
      (profile) => {
        if (!profile || registered) return;
        registered = true;
        registerPushToken(uid).catch(() => {});
      },
      () => {},
    );
  }, [uid]);

  const value = useMemo<Session>(() => {
    // The remembered choice if it's still paired; else the only one; else none.
    const device = devices?.find((d) => d.id === chosen) ?? (devices?.length === 1 ? devices[0]! : null);
    return {
      user,
      devices,
      deviceId: device?.id ?? null,
      device,
      devicesError,
      retryDevices: () => setAttempt((n) => n + 1),
      selectDevice: (id: string) => {
        setChosen(id);
        AsyncStorage.setItem(DEVICE_KEY, id).catch(() => {});
      },
    };
  }, [user, devices, chosen, devicesError]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error('useSession outside SessionProvider');
  return s;
}

/** For screens under (tabs): the device is guaranteed by the layout's gate. */
export function useDevice(): Device {
  const { device } = useSession();
  if (!device) throw new Error('useDevice without a selected device');
  return device;
}
