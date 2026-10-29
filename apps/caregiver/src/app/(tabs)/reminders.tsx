/**
 * Why this exists: set, change and cancel her reminders from afar.
 *
 * Time and date are typed ("9:30", "2026-11-04"), checked by the data
 * layer, and the result is said back in words ("Every Thursday"). Lost
 * for now: native date/time pickers — they behave differently on iOS,
 * Android and web, and the web one is what the e2e drives; a typed field
 * works the same everywhere. Worth revisiting with real caregivers.
 *
 * Cancelling asks once, inline (not an Alert, which does nothing on web):
 * a mis-tap shouldn't silently stop her blood-pressure reminder.
 * Cancelled reminders stay listed, greyed, so they can be brought back.
 */
import { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import {
  cancelReminder,
  describeRepeat,
  type Reminder,
  type ReminderErrors,
  ReminderValidationError,
  type Repeat,
  saveReminder,
  watchReminders,
} from '../../data';
import { useDevice } from '../../state/session';
import { Body, Button, Card, Choice, Empty, Field, Heading, Loading, Muted, Notice, Screen } from '../../ui/kit';
import { color, font } from '../../ui/theme';

const REPEAT_OPTIONS: { value: Repeat; label: string }[] = [
  { value: 'none', label: 'Once' },
  { value: 'daily', label: 'Every day' },
  { value: 'weekdays', label: 'Weekdays' },
  { value: 'weekly', label: 'Weekly' },
];

interface Draft {
  id: string | null;
  text: string;
  time: string;
  date: string;
  repeat: Repeat;
}

const blank: Draft = { id: null, text: '', time: '', date: '', repeat: 'daily' };

function ReminderForm({
  deviceId,
  initial,
  onDone,
}: {
  deviceId: string;
  initial: Draft;
  onDone: () => void;
}) {
  const [d, setD] = useState<Draft>(initial);
  const [errors, setErrors] = useState<ReminderErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    setErrors({});
    setFailure(null);
    try {
      await saveReminder(deviceId, { text: d.text, time: d.time, date: d.date, repeat: d.repeat }, d.id ?? undefined);
      onDone();
    } catch (e) {
      if (e instanceof ReminderValidationError) setErrors(e.errors);
      else setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const dateLabel = d.repeat === 'none' ? 'Day (optional)' : 'Starting from (optional)';
  const dateHint =
    d.repeat === 'none'
      ? 'Like 2026-11-04. Leave empty for the next time it comes round.'
      : d.repeat === 'weekly'
        ? 'Like 2026-11-04. It repeats on that day of the week; empty means today’s.'
        : 'Like 2026-11-04. Leave empty to start today.';

  return (
    <Card>
      <Heading>{d.id ? 'Change reminder' : 'New reminder'}</Heading>
      <Field
        label="What to remind her"
        placeholder="Take the blood pressure tablet"
        value={d.text}
        onChangeText={(text) => setD({ ...d, text })}
        error={errors.text}
      />
      <Field
        label="Time"
        hint="24-hour, her time. Like 09:30 or 18:00."
        placeholder="09:30"
        value={d.time}
        onChangeText={(time) => setD({ ...d, time })}
        keyboardType="numbers-and-punctuation"
        error={errors.time}
        maxLength={5}
      />
      <Choice label="How often" options={REPEAT_OPTIONS} value={d.repeat} onChange={(repeat) => setD({ ...d, repeat })} />
      {errors.repeat ? <Notice>{errors.repeat}</Notice> : null}
      <Field
        label={dateLabel}
        hint={dateHint}
        placeholder="YYYY-MM-DD"
        value={d.date}
        onChangeText={(date) => setD({ ...d, date })}
        keyboardType="numbers-and-punctuation"
        error={errors.date}
        maxLength={10}
      />
      {failure ? <Notice>Couldn’t save: {failure}</Notice> : null}
      <Button label={d.id ? 'Save changes' : 'Add reminder'} onPress={save} busy={saving} />
      <Button variant="link" label="Never mind" onPress={onDone} />
    </Card>
  );
}

function ReminderRow({ r, deviceId, onEdit }: { r: Reminder; deviceId: string; onEdit: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const how = describeRepeat(r);

  const cancel = async () => {
    setBusy(true);
    setFailure(null);
    try {
      await cancelReminder(deviceId, r.id);
      setConfirming(false);
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card style={!r.active && styles.inactive}>
      <View accessible accessibilityLabel={`${r.time}, ${r.text}, ${how}${r.active ? '' : ', cancelled'}`}>
        <Body style={styles.time}>
          {r.time} · {how}
        </Body>
        <Body style={styles.text}>{r.text}</Body>
        {!r.active ? <Muted>Cancelled</Muted> : null}
      </View>
      {failure ? <Notice>Couldn’t cancel: {failure}</Notice> : null}
      {confirming ? (
        <>
          <Body>Stop reminding her about this?</Body>
          <Button variant="danger" label="Yes, cancel it" onPress={cancel} busy={busy} />
          <Button variant="link" label="Keep it" onPress={() => setConfirming(false)} />
        </>
      ) : (
        <View style={styles.actions}>
          <Button
            variant="secondary"
            label={r.active ? 'Change' : 'Turn back on'}
            accessibilityLabel={`${r.active ? 'Change' : 'Turn back on'}: ${r.text}`}
            onPress={onEdit}
            style={styles.action}
          />
          {r.active ? (
            <Button
              variant="secondary"
              label="Cancel"
              accessibilityLabel={`Cancel reminder: ${r.text}`}
              onPress={() => setConfirming(true)}
              style={styles.action}
            />
          ) : null}
        </View>
      )}
    </Card>
  );
}

export default function Reminders() {
  const device = useDevice();
  const [reminders, setReminders] = useState<Reminder[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Draft | null>(null);

  useEffect(() => {
    setReminders(null);
    setEditing(null);
    setLoadError(null);
    return watchReminders(device.id, setReminders, (e) => setLoadError(e.message));
  }, [device.id]);

  return (
    <Screen>
      {editing ? (
        <ReminderForm key={editing.id ?? 'new'} deviceId={device.id} initial={editing} onDone={() => setEditing(null)} />
      ) : (
        <Button label="Add a reminder" onPress={() => setEditing(blank)} />
      )}
      {loadError ? <Notice>Couldn’t load reminders: {loadError}</Notice> : null}
      {!reminders && !loadError ? <Loading label="Loading reminders" /> : null}
      {reminders && reminders.length === 0 ? <Empty>No reminders yet.</Empty> : null}
      {reminders?.map((r) => (
        <ReminderRow
          key={r.id}
          r={r}
          deviceId={device.id}
          onEdit={() => setEditing({ id: r.id, text: r.text, time: r.time, date: r.date ?? '', repeat: r.repeat })}
        />
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  // Not opacity: that would drop the text below AA contrast.
  inactive: { backgroundColor: color.bg, borderStyle: 'dashed' },
  time: { fontSize: font.small, fontWeight: '700', color: color.muted },
  text: { fontWeight: '600' },
  actions: { flexDirection: 'row', gap: 12, flexWrap: 'wrap' },
  action: { flexGrow: 1 },
});
