/**
 * Why this exists: the device trusts what the app writes, and the rules
 * reject malformed documents outright. So every check made before a
 * write is pinned here: times, dates, pairing codes, message length, and
 * the forgiving converters on read. A loosened check then fails a test,
 * before it fails on her tablet or as a confusing permission error.
 */
import { authErrorMessage } from '../src/data/auth';
import { normalizePairCode } from '../src/data/devices';
import { MAX_MESSAGE_LENGTH, validateMessage } from '../src/data/messages';
import { describeRepeat, validateReminder } from '../src/data/reminders';
import { nextOccurrenceDate, normalizeTime, parseDate, parseTime } from '../src/data/time';
import { toEvent, toReminder } from '../src/data/types';

const NOW = new Date(2026, 9, 29, 14, 0); // Thu 29 Oct 2026, 14:00

describe('time helpers', () => {
  test('normalizeTime accepts what people type and rejects nonsense', () => {
    expect(normalizeTime('9:30')).toBe('09:30');
    expect(normalizeTime('09:05')).toBe('09:05');
    expect(normalizeTime('9.30')).toBe('09:30');
    expect(normalizeTime('0930')).toBe('09:30');
    expect(normalizeTime('930')).toBe('09:30');
    expect(normalizeTime('24:00')).toBeNull();
    expect(normalizeTime('12:60')).toBeNull();
    expect(normalizeTime('9')).toBeNull();
    expect(normalizeTime('nine')).toBeNull();
  });

  test('parseTime / parseDate are strict', () => {
    expect(parseTime('23:59')).toBe(23 * 60 + 59);
    expect(parseTime('9:30')).toBeNull();
    expect(parseDate('2026-02-30')).toBeNull();
    expect(parseDate('2026-11-04')?.getDate()).toBe(4);
  });

  test('nextOccurrenceDate is today if still ahead, else tomorrow', () => {
    expect(nextOccurrenceDate('18:00', NOW)).toBe('2026-10-29');
    expect(nextOccurrenceDate('14:00', NOW)).toBe('2026-10-30');
    expect(nextOccurrenceDate('09:00', NOW)).toBe('2026-10-30');
  });
});

describe('validateReminder', () => {
  test('normalises a good one-off and fills its date', () => {
    const v = validateReminder({ text: '  Tablets ', time: '9:00', repeat: 'none' }, NOW);
    expect(v.fields).toEqual({ text: 'Tablets', time: '09:00', date: '2026-10-30', repeat: 'none' });
  });

  test('a weekly reminder without a date repeats on today’s weekday', () => {
    expect(validateReminder({ text: 'Call', time: '10:00', repeat: 'weekly' }, NOW).fields?.date).toBe('2026-10-29');
  });

  test('daily keeps no date unless one is given', () => {
    expect(validateReminder({ text: 'Walk', time: '17:00', repeat: 'daily' }, NOW).fields?.date).toBeNull();
    expect(validateReminder({ text: 'Walk', time: '17:00', repeat: 'daily', date: '2026-11-02' }, NOW).fields?.date).toBe(
      '2026-11-02',
    );
  });

  test('rejects empty text, bad time, impossible or past dates, unknown repeat', () => {
    const v = validateReminder({ text: ' ', time: '25:00', repeat: 'none', date: '2026-10-28' }, NOW);
    expect(v.fields).toBeNull();
    expect(Object.keys(v.errors ?? {}).sort()).toEqual(['date', 'text', 'time']);
    expect(validateReminder({ text: 'x', time: '09:00', repeat: 'none', date: '2026-02-30' }, NOW).errors?.date).toBeDefined();
    expect(validateReminder({ text: 'x', time: '09:00', repeat: 'hourly' as never }, NOW).errors?.repeat).toBeDefined();
  });

  test('a one-off today must be later than now', () => {
    expect(validateReminder({ text: 'x', time: '13:59', repeat: 'none', date: '2026-10-29' }, NOW).errors?.time).toMatch(
      /already passed/,
    );
    expect(validateReminder({ text: 'x', time: '14:00', repeat: 'none', date: '2026-10-29' }, NOW).errors?.time).toBeDefined();
    expect(validateReminder({ text: 'x', time: '14:01', repeat: 'none', date: '2026-10-29' }, NOW).fields?.date).toBe(
      '2026-10-29',
    );
    // A daily one starting today at an earlier hour is fine: it's due tomorrow.
    expect(validateReminder({ text: 'x', time: '09:00', repeat: 'daily', date: '2026-10-29' }, NOW).fields).not.toBeNull();
  });

  test('a past start date on a repeat is fine (it simply already started)', () => {
    expect(validateReminder({ text: 'x', time: '09:00', repeat: 'daily', date: '2026-10-01' }, NOW).fields).not.toBeNull();
  });

  test('describeRepeat reads like a person would say it', () => {
    expect(describeRepeat({ repeat: 'daily', date: null })).toBe('Every day');
    expect(describeRepeat({ repeat: 'weekdays', date: null })).toBe('Monday to Friday');
    expect(describeRepeat({ repeat: 'weekly', date: '2026-10-29' })).toMatch(/^Every /);
  });
});

describe('pairing codes', () => {
  // Was six digits; 8 unambiguous characters since 2026-10-29 (owner's
  // call: six digits could be guessed within a code's 10 minutes).
  test('accepts typed and scanned forms, in any case', () => {
    expect(normalizePairCode('K7QM4XHP')).toBe('K7QM4XHP');
    expect(normalizePairCode(' k7qm 4xhp ')).toBe('K7QM4XHP');
    expect(normalizePairCode('K7QM-4XHP')).toBe('K7QM4XHP');
    expect(normalizePairCode('saathi-pair:K7QM4XHP')).toBe('K7QM4XHP');
  });

  test('rejects the old six digits, wrong lengths and look-alikes', () => {
    expect(normalizePairCode('123456')).toBeNull();
    expect(normalizePairCode('K7QM4XH')).toBeNull();
    expect(normalizePairCode('K7QM4XHPP')).toBeNull();
    expect(normalizePairCode('K7QM4XH0')).toBeNull(); // 0, not O
    expect(normalizePairCode('K7QM4XHI')).toBeNull(); // I, not 1
    expect(normalizePairCode('other:K7QM4XHP')).toBeNull();
  });
});

describe('messages', () => {
  test('validateMessage', () => {
    expect(validateMessage('  ')).not.toBeNull();
    expect(validateMessage('Hello Ma')).toBeNull();
    expect(validateMessage('x'.repeat(MAX_MESSAGE_LENGTH + 1))).not.toBeNull();
  });
});

describe('converters', () => {
  test('unknown event kinds are dropped, not guessed', () => {
    expect(toEvent('e', { kind: 'exploded', text: 't' })).toBeNull();
    expect(toEvent('e', { kind: 'acked', text: 't', reminderId: 'r' })?.reminderId).toBe('r');
  });

  test('a malformed reminder reads as safe defaults', () => {
    const r = toReminder('r', { text: 7, repeat: 'sometimes' });
    expect(r).toEqual(expect.objectContaining({ text: '', repeat: 'none', active: true, date: null }));
  });
});

describe('authErrorMessage', () => {
  test('maps known codes to plain words', () => {
    expect(authErrorMessage({ code: 'auth/invalid-credential' })).toMatch(/don’t match/);
    expect(authErrorMessage(new Error('boom'))).toBe('boom');
    expect(authErrorMessage(null)).toMatch(/went wrong/);
  });
});
