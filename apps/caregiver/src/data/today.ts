/**
 * Why this exists: the question a caregiver opens the app to answer —
 * "did she take them today?" — is a join of two collections the device
 * writes separately: the reminders (what should happen) and the events
 * (what did). `combineToday` is that join as a pure function, so the
 * rules of it are tested without Firestore; `watchToday` feeds it live.
 *
 * The status is the *latest* of today's fired/acked/missed events for
 * each reminder: acked = taken, missed = missed, fired or nothing =
 * pending. The app never decides on its own that something was missed
 * because the clock passed — the device knows whether she was asked,
 * whether she answered, and when it gave up; the app only reports it.
 *
 * Events today for a reminder that's no longer in the list (cancelled
 * after it fired, or set by voice on the tablet and never synced) still
 * show, from the event's own text. A caregiver who saw "taken" at noon
 * shouldn't see it vanish because someone cancelled the reminder at one.
 */
import { collection, onSnapshot, orderBy, query, Timestamp, where } from 'firebase/firestore';

import { services } from '../firebase';
import { addDays, hhmm, parseDate, startOfDay, ymd } from './time';
import { toEvent, toReminder, type DeviceEvent, type Reminder, type Unsubscribe } from './types';

export type TodayStatus = 'taken' | 'missed' | 'pending';

export interface TodayItem {
  /** Stable key for lists: the reminder id, or the orphan event's id. */
  key: string;
  reminderId: string | null;
  text: string;
  time: string;
  status: TodayStatus;
  /** The event the status came from; null when nothing has happened yet. */
  lastEvent: DeviceEvent | null;
  /** Null when the item comes only from an event (see module docstring). */
  reminder: Reminder | null;
}

const REMINDER_EVENT_KINDS = new Set(['fired', 'acked', 'missed']);

/** Does `r` fall on the local calendar day of `day`? Inactive never does. */
export function isDueOn(r: Reminder, day: Date): boolean {
  if (!r.active) return false;
  const today = ymd(day);
  // A date on a repeating reminder is its first day: nothing before it.
  if (r.repeat !== 'none' && r.date && today < r.date) return false;
  switch (r.repeat) {
    case 'none':
      // Undated one-off: the device resolves it to the next HH:MM; while
      // active it's today's business (or at the latest tomorrow morning's).
      return r.date ? r.date === today : true;
    case 'daily':
      return true;
    case 'weekdays': {
      const dow = day.getDay();
      return dow >= 1 && dow <= 5;
    }
    case 'weekly': {
      const anchor = (r.date && parseDate(r.date)) || r.updatedAt;
      return anchor ? anchor.getDay() === day.getDay() : false;
    }
  }
}

function statusOf(e: DeviceEvent | null): TodayStatus {
  if (e?.kind === 'acked') return 'taken';
  if (e?.kind === 'missed') return 'missed';
  return 'pending';
}

/** Today's reminders, each with its latest event of the day. Sorted by time. */
export function combineToday(reminders: Reminder[], events: DeviceEvent[], now: Date): TodayItem[] {
  const from = startOfDay(now).getTime();
  const to = addDays(startOfDay(now), 1).getTime();

  const latest = new Map<string, DeviceEvent>();
  for (const e of events) {
    if (!e.reminderId || !e.ts || !REMINDER_EVENT_KINDS.has(e.kind)) continue;
    const t = e.ts.getTime();
    if (t < from || t >= to) continue;
    const prev = latest.get(e.reminderId);
    if (!prev || (prev.ts && prev.ts.getTime() <= t)) latest.set(e.reminderId, e);
  }

  const items: TodayItem[] = [];
  const seen = new Set<string>();
  for (const r of reminders) {
    const ev = latest.get(r.id) ?? null;
    if (!isDueOn(r, now) && !ev) continue;
    seen.add(r.id);
    items.push({ key: r.id, reminderId: r.id, text: r.text, time: r.time, status: statusOf(ev), lastEvent: ev, reminder: r });
  }
  for (const [rid, ev] of latest) {
    if (seen.has(rid)) continue;
    items.push({
      key: `event:${ev.id}`,
      reminderId: rid,
      text: ev.text,
      time: ev.ts ? hhmm(ev.ts) : '',
      status: statusOf(ev),
      lastEvent: ev,
      reminder: null,
    });
  }
  return items.sort((a, b) => a.time.localeCompare(b.time) || a.text.localeCompare(b.text));
}

export interface TodaySummary {
  taken: number;
  missed: number;
  pending: number;
}

export function summarize(items: TodayItem[]): TodaySummary {
  const s: TodaySummary = { taken: 0, missed: 0, pending: 0 };
  for (const i of items) s[i.status] += 1;
  return s;
}

/**
 * Live Today for a device. Re-subscribes when the local day changes, so
 * an app left open overnight starts the new day empty instead of showing
 * yesterday's ticks against today's list.
 */
export function watchToday(
  deviceId: string,
  cb: (items: TodayItem[]) => void,
  onError?: (e: Error) => void,
  clock: () => Date = () => new Date(),
): Unsubscribe {
  const { db } = services();
  let reminders: Reminder[] = [];
  let events: DeviceEvent[] = [];
  let haveReminders = false;
  let haveEvents = false;
  let stopEvents: Unsubscribe = () => {};
  let day = '';

  const emit = () => {
    if (haveReminders && haveEvents) cb(combineToday(reminders, events, clock()));
  };
  const fail = (e: Error) => onError?.(e);

  const stopReminders = onSnapshot(
    collection(db, 'devices', deviceId, 'reminders'),
    (snap) => {
      reminders = snap.docs.map((d) => toReminder(d.id, d.data({ serverTimestamps: 'estimate' })));
      haveReminders = true;
      emit();
    },
    fail,
  );

  const subscribeDay = () => {
    stopEvents();
    events = [];
    haveEvents = false;
    const now = clock();
    day = ymd(now);
    const q = query(
      collection(db, 'devices', deviceId, 'events'),
      where('ts', '>=', Timestamp.fromDate(startOfDay(now))),
      orderBy('ts', 'asc'),
    );
    stopEvents = onSnapshot(
      q,
      (snap) => {
        events = snap.docs.flatMap((d) => {
          const e = toEvent(d.id, d.data({ serverTimestamps: 'estimate' }));
          return e ? [e] : [];
        });
        haveEvents = true;
        emit();
      },
      fail,
    );
  };
  subscribeDay();

  // A check every minute, not one timer set for midnight. A phone
  // suspends JS timers while the app is in the background, so a timer
  // armed at 22:00 might not have fired by 08:00 when the app is opened
  // again. This check runs within a minute of the app coming back.
  const dayCheck = setInterval(() => {
    if (ymd(clock()) !== day) subscribeDay();
  }, 60 * 1000);

  return () => {
    clearInterval(dayCheck);
    stopEvents();
    stopReminders();
  };
}
