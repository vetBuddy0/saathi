/**
 * Why this exists: the root of expo-router. File-based routes because a
 * screen is then a file you can find by its URL, the web build gets real
 * paths for free, and deep links ("open Messages") need no table.
 * Lost: React Navigation configured by hand (the same library underneath,
 * with the route table to maintain ourselves).
 */
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

import { SessionProvider } from '../state/session';
import { color, font } from '../ui/theme';

export default function RootLayout() {
  return (
    <SessionProvider>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: color.bg },
          headerTintColor: color.primary,
          headerTitleStyle: { fontSize: font.heading, fontWeight: '700', color: color.text },
          headerShadowVisible: false,
          contentStyle: { backgroundColor: color.bg },
        }}
      >
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="sign-in" options={{ title: 'Saathi Family', headerBackVisible: false }} />
        <Stack.Screen name="pair" options={{ title: 'Pair with Saathi' }} />
        <Stack.Screen name="devices" options={{ title: 'Choose who to look in on' }} />
        <Stack.Screen name="settings" options={{ title: 'Settings' }} />
      </Stack>
    </SessionProvider>
  );
}
