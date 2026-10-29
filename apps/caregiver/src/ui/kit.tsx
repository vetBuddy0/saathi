/**
 * Why this exists: a handful of components that make the accessible
 * thing the default — every button has a role and a label, every field
 * a label a screen reader announces, every error a live region — so a
 * screen written in a hurry is still usable with TalkBack/VoiceOver and
 * at 200% text size. Lost: a component library (react-native-paper and
 * friends): their defaults are dense Material type, and overriding every
 * size is more code than this file.
 */
import { type ReactNode } from 'react';
import {
  ActivityIndicator,
  Pressable,
  type PressableProps,
  ScrollView,
  type ScrollViewProps,
  StyleSheet,
  Text,
  TextInput,
  type TextInputProps,
  type TextProps,
  View,
  type ViewProps,
} from 'react-native';

import { color, font, radius, space, touch } from './theme';

export function Screen({ children, scroll = true, ...rest }: { children: ReactNode; scroll?: boolean } & ScrollViewProps) {
  if (!scroll) return <View style={styles.screen}>{children}</View>;
  return (
    <ScrollView
      style={styles.screenScroll}
      contentContainerStyle={styles.screenContent}
      keyboardShouldPersistTaps="handled"
      {...rest}
    >
      {children}
    </ScrollView>
  );
}

export function Title({ children, style, ...rest }: TextProps) {
  return (
    <Text accessibilityRole="header" style={[styles.title, style]} {...rest}>
      {children}
    </Text>
  );
}

export function Heading({ children, style, ...rest }: TextProps) {
  return (
    <Text accessibilityRole="header" style={[styles.heading, style]} {...rest}>
      {children}
    </Text>
  );
}

export function Body({ children, style, ...rest }: TextProps) {
  return (
    <Text style={[styles.body, style]} {...rest}>
      {children}
    </Text>
  );
}

export function Muted({ children, style, ...rest }: TextProps) {
  return (
    <Text style={[styles.muted, style]} {...rest}>
      {children}
    </Text>
  );
}

export function Card({ children, style, ...rest }: ViewProps) {
  return (
    <View style={[styles.card, style]} {...rest}>
      {children}
    </View>
  );
}

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'link';

export function Button({
  label,
  onPress,
  variant = 'primary',
  busy = false,
  disabled = false,
  accessibilityLabel,
  accessibilityHint,
  style,
  testID,
}: {
  label: string;
  onPress: () => void;
  variant?: ButtonVariant;
  busy?: boolean;
  disabled?: boolean;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  style?: PressableProps['style'];
  testID?: string;
}) {
  const off = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: off, busy }}
      disabled={off}
      onPress={onPress}
      testID={testID}
      style={(state) => [
        styles.button,
        styles[`button_${variant}`],
        state.pressed && styles.pressed,
        off && styles.disabled,
        typeof style === 'function' ? style(state) : style,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={variant === 'primary' || variant === 'danger' ? color.onPrimary : color.primary} />
      ) : (
        <Text style={[styles.buttonText, styles[`buttonText_${variant}`]]}>{label}</Text>
      )}
    </Pressable>
  );
}

export function Field({
  label,
  error,
  hint,
  style,
  ...input
}: { label: string; error?: string | null; hint?: string } & TextInputProps) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      {hint ? <Muted style={styles.hint}>{hint}</Muted> : null}
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={hint}
        placeholderTextColor={color.placeholder}
        style={[styles.input, error ? styles.inputError : null, style]}
        {...input}
      />
      {error ? (
        <Text accessibilityLiveRegion="polite" role="alert" style={styles.error}>
          {error}
        </Text>
      ) : null}
    </View>
  );
}

/** A row of large toggle buttons for picking one of a few options. */
export function Choice<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <View style={styles.field} accessibilityRole="radiogroup" accessibilityLabel={label}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.choiceRow}>
        {options.map((o) => {
          const selected = o.value === value;
          return (
            <Pressable
              key={o.value}
              accessibilityRole="radio"
              accessibilityLabel={o.label}
              accessibilityState={{ checked: selected }}
              onPress={() => onChange(o.value)}
              style={[styles.choice, selected && styles.choiceOn]}
            >
              <Text style={[styles.choiceText, selected && styles.choiceTextOn]}>{o.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

export function Notice({ kind = 'error', children }: { kind?: 'error' | 'info'; children: ReactNode }) {
  return (
    <View
      role={kind === 'error' ? 'alert' : undefined}
      accessibilityLiveRegion="polite"
      style={[styles.notice, kind === 'error' ? styles.noticeError : styles.noticeInfo]}
    >
      <Text style={[styles.body, kind === 'error' && { color: color.danger }]}>{children}</Text>
    </View>
  );
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <View style={styles.loading} accessibilityLabel={label} accessibilityRole="progressbar">
      <ActivityIndicator size="large" color={color.primary} />
    </View>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <Card>
      <Body style={{ color: color.muted }}>{children}</Body>
    </Card>
  );
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: color.bg, padding: space.md },
  screenScroll: { flex: 1, backgroundColor: color.bg },
  screenContent: { padding: space.md, paddingBottom: space.xl * 2, gap: space.md, maxWidth: 720, width: '100%', alignSelf: 'center' },
  title: { fontSize: font.title, fontWeight: '700', color: color.text, lineHeight: font.title * 1.25 },
  heading: { fontSize: font.heading, fontWeight: '700', color: color.text, lineHeight: font.heading * 1.3 },
  body: { fontSize: font.body, color: color.text, lineHeight: font.body * 1.4 },
  muted: { fontSize: font.small, color: color.muted, lineHeight: font.small * 1.4 },
  card: {
    backgroundColor: color.surface,
    borderRadius: radius,
    borderWidth: 1,
    borderColor: color.border,
    padding: space.md,
    gap: space.sm,
  },
  button: {
    minHeight: touch,
    borderRadius: radius,
    paddingHorizontal: space.lg,
    alignItems: 'center',
    justifyContent: 'center',
  },
  button_primary: { backgroundColor: color.primary },
  button_secondary: { backgroundColor: color.surface, borderWidth: 2, borderColor: color.primary },
  button_danger: { backgroundColor: color.danger },
  button_link: { backgroundColor: 'transparent', paddingHorizontal: space.sm },
  pressed: { opacity: 0.8 },
  disabled: { opacity: 0.5 },
  buttonText: { fontSize: font.body, fontWeight: '700' },
  buttonText_primary: { color: color.onPrimary },
  buttonText_secondary: { color: color.primary },
  buttonText_danger: { color: color.onPrimary },
  buttonText_link: { color: color.primary, textDecorationLine: 'underline' },
  field: { gap: space.xs },
  label: { fontSize: font.body, fontWeight: '600', color: color.text },
  hint: { marginBottom: space.xs },
  input: {
    minHeight: touch,
    fontSize: font.body,
    color: color.text,
    backgroundColor: color.surface,
    borderWidth: 2,
    borderColor: color.border,
    borderRadius: radius,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
  },
  inputError: { borderColor: color.danger },
  error: { fontSize: font.small, color: color.danger },
  choiceRow: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  choice: {
    minHeight: touch - 8,
    paddingHorizontal: space.md,
    borderRadius: radius,
    borderWidth: 2,
    borderColor: color.primary,
    backgroundColor: color.surface,
    justifyContent: 'center',
  },
  choiceOn: { backgroundColor: color.primary },
  choiceText: { fontSize: font.small + 1, color: color.primary, fontWeight: '600' },
  choiceTextOn: { color: color.onPrimary },
  notice: { borderRadius: radius, padding: space.md, borderWidth: 1 },
  noticeError: { backgroundColor: color.missedBg, borderColor: color.danger },
  noticeInfo: { backgroundColor: color.pendingBg, borderColor: color.border },
  loading: { flex: 1, minHeight: 200, alignItems: 'center', justifyContent: 'center', backgroundColor: color.bg },
});
