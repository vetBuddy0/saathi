/**
 * Why this exists: caregivers set and change her reminders from afar;
 * the device picks them up and says them when due.
 *
 * Cancel is `active: false`, never a delete: the device may already hold
 * the reminder (or be mid-way through saying it), and a flag it can see
 * on its next sync is unambiguous where a missing doc is not — it can't
 * tell "deleted" from "not synced yet". It also keeps Today's history
 * honest about what was asked of her.
 *
 * Validation lives here, not in the form: the device trusts these docs,
 * so a time like "9" or a date like "2026-02-30" must never be written,
 * whichever screen (or test) calls `saveReminder`.
 *
 * Dates are filled in where leaving them out would be ambiguous: a
 * one-off with no date gets the next day that time happens, and a
 * weekly one gets today as the day of the week it repeats on. Without
 * that the caregiver could not see which day they had just set.
 */
import {
  addDoc,
  collection,
  deleteField,
  doc,
  onSnapshot,
  serverTimestamp,
  setDoc,
  updateDoc,
} from 'firebase/firestore';

import { services } from '../firebase';
import { nextOccurrenceDate, normalizeTime, parseDate, parseTime, ymd } from './time';
import { REPEATS, toReminder, type Reminder, type Repeat, type Unsubscribe } from './types';

export const MAX_REMINDER_LENGTH = 200;

export interface ReminderInput {
  text: string;
  /** "HH:MM" (loosely typed forms like "9:30" are accepted and normalised). */
  time: string;
  /** "YYYY-MM-DD", or empty for "the next time it comes round". */
  date?: string | null;
  repeat: Repeat;
}

export interface ReminderFields {
  text: string;
  time: string;
  date: string | null;
  repeat: Repeat;
}

export type ReminderErrors = Partial<Record<'text' | 'time' | 'date' | 'repeat', string>>;

/** Check and normalise. Either `fields` is set or `errors` says why not. */
export function validateReminder(
  input: ReminderInput,
  now: Date = new Date(),
): { fields: ReminderFields; errors: null } | { fields: null; errors: ReminderErrors } {
  const errors: ReminderErrors = {};
  const text = input.text.trim();
  if (!text) errors.text = 'What should Saathi remind her about?';
  else if (text.length > MAX_REMINDER_LENGTH) errors.text = `Please keep it under ${MAX_REMINDER_LENGTH} letters.`;

  const time = normalizeTime(input.time);
  if (!time) errors.time = 'Please give a time like 09:30 or 18:00.';

  if (!REPEATS.includes(input.repeat)) errors.repeat = 'Please choose how often.';

  const rawDate = (input.date ?? '').trim();
  let date: string | null = null;
  if (rawDate) {
    const parsed = parseDate(rawDate);
    if (!parsed) errors.date = 'Please give a date like 2026-11-04.';
    else if (input.repeat === 'none' && ymd(parsed) < ymd(now)) errors.date = 'That day has already passed.';
    else date = ymd(parsed);
  }

  // A one-off today at a time already gone would sit as "Not yet" all day
  // and never be said.
  if (time && date && input.repeat === 'none' && date === ymd(now)) {
    const minutes = parseTime(time);
    if (minutes !== null && minutes <= now.getHours() * 60 + now.getMinutes()) {
      errors.time = 'That time has already passed today. Choose a later time, or another day.';
    }
  }

  if (Object.keys(errors).length > 0 || !time) return { fields: null, errors };

  if (!date && input.repeat === 'none') date = nextOccurrenceDate(time, now);
  if (!date && input.repeat === 'weekly') date = ymd(now);
  return { fields: { text, time, date, repeat: input.repeat }, errors: null };
}

export class ReminderValidationError extends Error {
  constructor(public readonly errors: ReminderErrors) {
    super(Object.values(errors).join(' '));
    this.name = 'ReminderValidationError';
  }
}

export function watchReminders(deviceId: string, cb: (reminders: Reminder[]) => void, onError?: (e: Error) => void): Unsubscribe {
  return onSnapshot(
    collection(services().db, 'devices', deviceId, 'reminders'),
    (snap) =>
      cb(
        snap.docs
          .map((d) => toReminder(d.id, d.data({ serverTimestamps: 'estimate' })))
          .sort((a, b) => Number(b.active) - Number(a.active) || a.time.localeCompare(b.time) || a.text.localeCompare(b.text)),
      ),
    (e) => onError?.(e),
  );
}

/**
 * Create (no `id`) or change (with `id`) a reminder. Saving always makes
 * it active again — editing a cancelled one is how you bring it back.
 * Resolves to the reminder id.
 */
export async function saveReminder(deviceId: string, input: ReminderInput, id?: string, now: Date = new Date()): Promise<string> {
  const v = validateReminder(input, now);
  if (!v.fields) throw new ReminderValidationError(v.errors);
  const { auth, db } = services();
  const uid = auth.currentUser?.uid;
  if (!uid) throw new Error('Please sign in first.');
  const col = collection(db, 'devices', deviceId, 'reminders');
  const { date, ...rest } = v.fields;
  // `date` is optional in the contract: absent, never null.
  const body = { ...rest, active: true, updatedAt: serverTimestamp() };
  if (id) {
    // merge keeps `createdBy` as whoever first made it.
    await setDoc(doc(col, id), { ...body, date: date ?? deleteField() }, { merge: true });
    return id;
  }
  const ref = await addDoc(col, { ...body, ...(date ? { date } : {}), createdBy: uid });
  return ref.id;
}

export async function cancelReminder(deviceId: string, id: string): Promise<void> {
  await updateDoc(doc(services().db, 'devices', deviceId, 'reminders', id), {
    active: false,
    updatedAt: serverTimestamp(),
  });
}

/** How a repeat reads to a person. */
export function describeRepeat(r: Pick<Reminder, 'repeat' | 'date'>): string {
  const day = r.date ? parseDate(r.date) : null;
  switch (r.repeat) {
    case 'none':
      return day ? `Once, ${day.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}` : 'Once';
    case 'daily':
      return 'Every day';
    case 'weekdays':
      return 'Monday to Friday';
    case 'weekly':
      return day ? `Every ${day.toLocaleDateString(undefined, { weekday: 'long' })}` : 'Every week';
  }
}
