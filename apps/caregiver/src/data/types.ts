/**
 * Why this exists: the shared Firestore contract, written down once as
 * the shapes the app reads, with the conversion from raw documents in the
 * same place. Screens never see a `Timestamp` or a `DocumentSnapshot` —
 * if the contract moves, this file and its converters move, not the UI.
 *
 * Converters are forgiving on read (a missing field becomes a safe
 * default, not a crash): documents are written by the device, the Cloud
 * Functions and this app, and one malformed doc must not blank a screen
 * her son is looking at.
 */
export type Repeat = 'none' | 'daily' | 'weekdays' | 'weekly';
export const REPEATS: readonly Repeat[] = ['none', 'daily', 'weekdays', 'weekly'];

export type EventKind = 'fired' | 'acked' | 'missed' | 'timer_done' | 'message_spoken';

export interface Device {
  id: string;
  name: string;
  caregivers: string[];
  deviceUid: string | null;
  createdAt: Date | null;
}

export interface Turn {
  id: string;
  ts: Date | null;
  heard: string;
  said: string;
  language: string;
  clipPath: string | null;
}

export interface DeviceEvent {
  id: string;
  ts: Date | null;
  kind: EventKind;
  reminderId: string | null;
  text: string;
}

export interface Message {
  id: string;
  from: string;
  fromUid: string;
  text: string;
  createdAt: Date | null;
  spokenAt: Date | null;
}

export interface Reply {
  id: string;
  ts: Date | null;
  text: string;
}

export interface Reminder {
  id: string;
  text: string;
  /** "HH:MM", her local time on the device. */
  time: string;
  /** "YYYY-MM-DD": the day of a one-off, the first day of a repeat. */
  date: string | null;
  repeat: Repeat;
  active: boolean;
  createdBy: string;
  updatedAt: Date | null;
}

export interface Profile {
  name: string;
  fcmTokens: string[];
}

type Raw = Record<string, unknown>;

/** A Firestore Timestamp (or anything with toDate), a Date, or nothing. */
export function toDate(v: unknown): Date | null {
  if (v instanceof Date) return v;
  if (v && typeof (v as { toDate?: unknown }).toDate === 'function') {
    return (v as { toDate: () => Date }).toDate();
  }
  return null;
}

const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback);
const strOrNull = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);

export function toDevice(id: string, d: Raw): Device {
  return {
    id,
    name: str(d.name, 'Saathi'),
    caregivers: Array.isArray(d.caregivers) ? d.caregivers.filter((x): x is string => typeof x === 'string') : [],
    deviceUid: strOrNull(d.deviceUid),
    createdAt: toDate(d.createdAt),
  };
}

export function toTurn(id: string, d: Raw): Turn {
  return {
    id,
    ts: toDate(d.ts),
    heard: str(d.heard),
    said: str(d.said),
    language: str(d.language),
    clipPath: strOrNull(d.clipPath),
  };
}

const EVENT_KINDS: readonly EventKind[] = ['fired', 'acked', 'missed', 'timer_done', 'message_spoken'];

export function toEvent(id: string, d: Raw): DeviceEvent | null {
  const kind = d.kind as EventKind;
  if (!EVENT_KINDS.includes(kind)) return null; // a kind from a newer device: skip, don't guess
  return { id, ts: toDate(d.ts), kind, reminderId: strOrNull(d.reminderId), text: str(d.text) };
}

export function toMessage(id: string, d: Raw): Message {
  return {
    id,
    from: str(d.from),
    fromUid: str(d.fromUid),
    text: str(d.text),
    createdAt: toDate(d.createdAt),
    spokenAt: toDate(d.spokenAt),
  };
}

export function toReply(id: string, d: Raw): Reply {
  return { id, ts: toDate(d.ts), text: str(d.text) };
}

export function toReminder(id: string, d: Raw): Reminder {
  const repeat = REPEATS.includes(d.repeat as Repeat) ? (d.repeat as Repeat) : 'none';
  return {
    id,
    text: str(d.text),
    time: str(d.time, '00:00'),
    date: strOrNull(d.date),
    repeat,
    active: d.active !== false,
    createdBy: str(d.createdBy),
    updatedAt: toDate(d.updatedAt),
  };
}

export function toProfile(d: Raw | undefined): Profile {
  return {
    name: str(d?.name),
    fcmTokens: Array.isArray(d?.fcmTokens) ? (d.fcmTokens as unknown[]).filter((x): x is string => typeof x === 'string') : [],
  };
}

/** What a watcher returns: call it to stop listening. */
export type Unsubscribe = () => void;
