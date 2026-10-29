/**
 * Why this exists: "taken / missed / not yet" is what a worried son acts
 * on, and the join behind it has edge cases: midnight, weekly anchors,
 * cancelled reminders that already fired, and events with no reminder
 * doc. The emulator can't reach those quickly. As a pure function it
 * can be tested exhaustively and in milliseconds.
 */
import { combineToday, isDueOn, summarize } from '../src/data/today';
import type { DeviceEvent, Reminder } from '../src/data/types';

// Local-time constructors throughout: the rules are about her calendar
// day, so the tests must not depend on the machine's timezone.
const THU = new Date(2026, 9, 29, 14, 0); // Thursday 29 Oct 2026, 14:00
const SAT = new Date(2026, 9, 31, 10, 0);

function rem(id: string, over: Partial<Reminder> = {}): Reminder {
  return {
    id,
    text: `reminder ${id}`,
    time: '09:00',
    date: null,
    repeat: 'daily',
    active: true,
    createdBy: 'u1',
    updatedAt: new Date(2026, 9, 1),
    ...over,
  };
}

function ev(id: string, reminderId: string | null, kind: DeviceEvent['kind'], ts: Date, text = 'x'): DeviceEvent {
  return { id, reminderId, kind, ts, text };
}

describe('isDueOn', () => {
  test('daily is due every day, inactive never', () => {
    expect(isDueOn(rem('a'), THU)).toBe(true);
    expect(isDueOn(rem('a', { active: false }), THU)).toBe(false);
  });

  test('a one-off is due on its date only; undated is due while active', () => {
    expect(isDueOn(rem('a', { repeat: 'none', date: '2026-10-29' }), THU)).toBe(true);
    expect(isDueOn(rem('a', { repeat: 'none', date: '2026-10-30' }), THU)).toBe(false);
    expect(isDueOn(rem('a', { repeat: 'none', date: null }), THU)).toBe(true);
  });

  test('weekdays skips the weekend', () => {
    expect(isDueOn(rem('a', { repeat: 'weekdays' }), THU)).toBe(true);
    expect(isDueOn(rem('a', { repeat: 'weekdays' }), SAT)).toBe(false);
  });

  test('weekly repeats on its date’s weekday, falling back to updatedAt', () => {
    expect(isDueOn(rem('a', { repeat: 'weekly', date: '2026-10-22' }), THU)).toBe(true); // a Thursday
    expect(isDueOn(rem('a', { repeat: 'weekly', date: '2026-10-23' }), THU)).toBe(false); // a Friday
    expect(isDueOn(rem('a', { repeat: 'weekly', date: null, updatedAt: new Date(2026, 9, 15) }), THU)).toBe(true);
    expect(isDueOn(rem('a', { repeat: 'weekly', date: null, updatedAt: null }), THU)).toBe(false);
  });

  test('a repeat does not start before its first date', () => {
    expect(isDueOn(rem('a', { repeat: 'daily', date: '2026-11-01' }), THU)).toBe(false);
    expect(isDueOn(rem('a', { repeat: 'daily', date: '2026-10-29' }), THU)).toBe(true);
  });
});

describe('combineToday', () => {
  test('status comes from the latest of today’s events per reminder', () => {
    const reminders = [rem('tabs', { time: '09:00' }), rem('walk', { time: '17:00' }), rem('bp', { time: '12:00' })];
    const events = [
      ev('e1', 'tabs', 'fired', new Date(2026, 9, 29, 9, 0)),
      ev('e2', 'tabs', 'acked', new Date(2026, 9, 29, 9, 2)),
      ev('e3', 'bp', 'fired', new Date(2026, 9, 29, 12, 0)),
      ev('e4', 'bp', 'missed', new Date(2026, 9, 29, 12, 30)),
    ];
    const items = combineToday(reminders, events, THU);
    expect(items.map((i) => [i.reminderId, i.status])).toEqual([
      ['tabs', 'taken'],
      ['bp', 'missed'],
      ['walk', 'pending'],
    ]);
    expect(items[0]!.lastEvent?.id).toBe('e2');
    expect(items[2]!.lastEvent).toBeNull();
    expect(summarize(items)).toEqual({ taken: 1, missed: 1, pending: 1 });
  });

  test('fired but not yet answered is pending, with the event kept', () => {
    const items = combineToday([rem('a')], [ev('e1', 'a', 'fired', new Date(2026, 9, 29, 9, 0))], THU);
    expect(items[0]!.status).toBe('pending');
    expect(items[0]!.lastEvent?.kind).toBe('fired');
  });

  test('the latest event wins regardless of input order', () => {
    const events = [
      ev('late', 'a', 'acked', new Date(2026, 9, 29, 9, 30)),
      ev('early', 'a', 'missed', new Date(2026, 9, 29, 9, 10)),
    ];
    expect(combineToday([rem('a')], events, THU)[0]!.status).toBe('taken');
  });

  test('yesterday’s and tomorrow’s events do not count today', () => {
    const events = [
      ev('y', 'a', 'acked', new Date(2026, 9, 28, 23, 59)),
      ev('t', 'a', 'missed', new Date(2026, 9, 30, 0, 0)),
    ];
    const items = combineToday([rem('a')], events, THU);
    expect(items[0]!.status).toBe('pending');
  });

  test('non-reminder events and events without a reminder are ignored', () => {
    const events = [
      ev('t', null, 'timer_done', new Date(2026, 9, 29, 10, 0)),
      ev('m', null, 'message_spoken', new Date(2026, 9, 29, 10, 0)),
      ev('x', 'a', 'timer_done', new Date(2026, 9, 29, 10, 0)),
    ];
    const items = combineToday([rem('a')], events, THU);
    expect(items).toHaveLength(1);
    expect(items[0]!.status).toBe('pending');
  });

  test('a reminder not due today still shows if something happened to it today', () => {
    const notToday = rem('sat', { repeat: 'weekly', date: '2026-10-31' });
    const cancelled = rem('gone', { active: false, time: '08:00' });
    const events = [ev('e', 'gone', 'acked', new Date(2026, 9, 29, 8, 1))];
    const items = combineToday([notToday, cancelled], events, THU);
    expect(items.map((i) => [i.reminderId, i.status])).toEqual([['gone', 'taken']]);
  });

  test('an event for a reminder doc that does not exist shows from the event', () => {
    const events = [ev('e', 'voice-set', 'acked', new Date(2026, 9, 29, 7, 5), 'Drink water')];
    const items = combineToday([], events, THU);
    expect(items).toEqual([
      expect.objectContaining({ reminderId: 'voice-set', text: 'Drink water', time: '07:05', status: 'taken', reminder: null }),
    ]);
  });

  test('sorted by time, then text', () => {
    const items = combineToday(
      [rem('c', { time: '18:00', text: 'c' }), rem('b', { time: '08:00', text: 'b' }), rem('a', { time: '08:00', text: 'a' })],
      [],
      THU,
    );
    expect(items.map((i) => i.reminderId)).toEqual(['a', 'b', 'c']);
  });
});
