/**
 * Why this exists: reminders are stored as wall-clock strings ("09:00",
 * "2026-10-29") because that is what she and the device mean by them —
 * not as instants, which would shift when a caregiver abroad edits one.
 * These helpers are the only place those strings are parsed, all in the
 * runtime's local time, so every screen agrees on what "today" is.
 *
 * Known gap, raised rather than hidden: the contract carries no timezone
 * for the device, so "today" here is the caregiver's day. For family in
 * her timezone that is the same day; for a son in another one it is not.
 * The fix is a `tz` on `devices/{id}` — a contract change, so not made
 * here (see DECISIONS.md, 2026-10-29 caregiver app).
 */
const pad = (n: number): string => String(n).padStart(2, '0');

/** "YYYY-MM-DD" for the local calendar day of `d`. */
export function ymd(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "HH:MM" for the local time of `d`. */
export function hhmm(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export function addDays(d: Date, days: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + days, d.getHours(), d.getMinutes(), d.getSeconds());
}

/** Minutes after midnight for a valid "HH:MM", else null. */
export function parseTime(s: string): number | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(s.trim());
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/** A local Date for a real calendar "YYYY-MM-DD" (rejects 2026-02-30), else null. */
export function parseDate(s: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s.trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(y, mo - 1, d);
  if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d) return null;
  return date;
}

/** Accepts "9:5", "9.30", "0930" and the like; returns canonical "HH:MM" or null. */
export function normalizeTime(input: string): string | null {
  const s = input.trim().replace('.', ':');
  let h: number;
  let m: number;
  const colon = /^(\d{1,2}):(\d{1,2})$/.exec(s);
  const bare = /^(\d{3,4})$/.exec(s);
  if (colon) {
    h = Number(colon[1]);
    m = Number(colon[2]);
  } else if (bare) {
    const digits = bare[1]!.padStart(4, '0');
    h = Number(digits.slice(0, 2));
    m = Number(digits.slice(2));
  } else {
    return null;
  }
  if (h > 23 || m > 59) return null;
  return `${pad(h)}:${pad(m)}`;
}

/** The day a one-off at `time` next happens: today if still ahead, else tomorrow. */
export function nextOccurrenceDate(time: string, now: Date): string {
  const minutes = parseTime(time);
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  return ymd(minutes !== null && minutes > nowMinutes ? now : addDays(now, 1));
}
