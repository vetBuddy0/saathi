/**
 * Why this exists: the four things a caregiver comes back for, one tap
 * apart. Settings sits in the header rather than as a fifth tab: five
 * labels at a readable size don't fit a small phone, and Settings is the
 * one visited least. Labels are words with no icons — "Reminders" is
 * clearer to her daughter than any bell glyph, and it costs no icon font.
 *
 * The gate here means every tab screen can assume a signed-in caregiver
 * with a device chosen (`useDevice()`); if either goes away (sign-out, or
 * being removed from her device) the tabs unmount instead of crashing.
 */
import { Redirect, router } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';

import { useSession } from '../../state/session';
import { Button, Loading } from '../../ui/kit';
import { color, font } from '../../ui/theme';

export default function TabsLayout() {
  const { user, devices, device, devicesError } = useSession();
  // The gate at "/" owns every not-ready state, including a failed device list.
  if (devicesError) return <Redirect href="/" />;
  if (user === undefined || (user && devices === undefined)) return <Loading />;
  if (!user || !device) return <Redirect href="/" />;
  const many = (devices?.length ?? 0) > 1;

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: color.bg },
        headerShadowVisible: false,
        headerTitle: device.name,
        headerTitleStyle: { fontSize: font.heading, fontWeight: '700', color: color.text },
        headerTitleAlign: 'left',
        headerRight: () => (
          <Button
            variant="link"
            label="Settings"
            accessibilityHint="Your name, devices and signing out"
            onPress={() => router.push('/settings')}
          />
        ),
        headerLeft: many
          ? () => (
              <Button
                variant="link"
                label="Switch"
                accessibilityLabel="Switch device"
                accessibilityHint="Choose which Saathi to look at"
                onPress={() => router.push('/devices')}
              />
            )
          : undefined,
        sceneStyle: { backgroundColor: color.bg },
        tabBarActiveTintColor: color.primary,
        tabBarInactiveTintColor: color.muted,
        tabBarStyle: { height: 68, backgroundColor: color.surface, borderTopColor: color.border },
        tabBarIconStyle: { display: 'none' },
        tabBarIcon: () => null,
        tabBarItemStyle: { justifyContent: 'center', paddingHorizontal: 0 },
        // 16, not the body size: four words must fit a 360px-wide phone.
        tabBarLabelStyle: { fontSize: 16, fontWeight: '700', marginTop: 0 },
        tabBarLabelPosition: 'below-icon',
        headerRightContainerStyle: { paddingRight: 8 },
        headerLeftContainerStyle: { paddingLeft: 8 },
      }}
    >
      <Tabs.Screen name="today" options={{ title: 'Today', tabBarAccessibilityLabel: 'Today: her reminders' }} />
      <Tabs.Screen
        name="conversations"
        options={{ title: 'Talks', tabBarAccessibilityLabel: 'Conversations with Saathi' }}
      />
      <Tabs.Screen name="messages" options={{ title: 'Messages', tabBarAccessibilityLabel: 'Messages to her' }} />
      <Tabs.Screen name="reminders" options={{ title: 'Reminders', tabBarAccessibilityLabel: 'Manage reminders' }} />
    </Tabs>
  );
}
