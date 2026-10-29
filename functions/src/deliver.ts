/**
 * Why this exists: the steps around `buildNotifications` — read the
 * device and users, send, prune dead tokens — are the same for every
 * trigger, and they are where a quiet failure would hide (a token that
 * never gets pruned keeps failing forever). They take their I/O as
 * `Store` and `Sender` so the whole path runs in unit tests with fakes,
 * and the emulator e2e swaps only the `Sender`.
 *
 * Contested:
 * - A missed event is filtered *before* reading the device. Lost: one
 *   generic path that reads first. Every `fired`/`acked` event would cost
 *   a device read for nothing, and those are most of the traffic.
 * - Only "this token is dead" errors prune (`DEAD_TOKEN_CODES`). Lost:
 *   pruning on any per-token failure. Quota or transient errors would
 *   wipe a caregiver's working phone and they would silently stop hearing
 *   about missed medicine. `messaging/invalid-argument` is deliberately
 *   not in the set: FCM also returns it for a bad *payload*, and a payload
 *   bug would then prune every caregiver's token at once.
 * - Retries are on (see index.ts), so only a failed *send* throws. A
 *   failed prune is logged and swallowed: retrying for it would re-send
 *   every push that already went out.
 * - No idempotency key. Lost: a `_push_sent/{eventId}` marker per trigger.
 *   A marker written before sending turns a crash into a lost alert; one
 *   written after still duplicates on a crash mid-send. For a missed
 *   medicine a rare duplicate push is the better failure, and it costs no
 *   extra collection outside the shared contract.
 */

import {
  buildNotifications,
  DeviceDoc,
  EventDoc,
  isNotifiableEvent,
  MessageDoc,
  Notification,
  recipientUids,
  ReplyDoc,
  spokenNow,
  Trigger,
  UserDoc,
} from "./notify";

export interface SendOutcome {
  uid: string;
  token: string;
  ok: boolean;
  /** FCM error code, e.g. `messaging/registration-token-not-registered`. */
  errorCode?: string;
}

/** Delivers notifications; one outcome per input, same order. */
export interface Sender {
  send(notifications: Notification[]): Promise<SendOutcome[]>;
}

export interface Store {
  getDevice(deviceId: string): Promise<DeviceDoc | undefined>;
  getMessage(deviceId: string, msgId: string): Promise<MessageDoc | undefined>;
  getUsers(uids: string[]): Promise<Record<string, UserDoc | undefined>>;
  /** Remove these tokens from `users/{uid}.fcmTokens`; a missing user is fine. */
  removeTokens(uid: string, tokens: string[]): Promise<void>;
}

export interface Deps {
  store: Store;
  sender: Sender;
}

/** Errors that mean the token will never work again. `not-registered` is
 *  the app uninstalled / token rotated; `invalid-registration-token` is a
 *  malformed token that was stored by mistake. */
export const DEAD_TOKEN_CODES: ReadonlySet<string> = new Set([
  "messaging/registration-token-not-registered",
  "messaging/invalid-registration-token",
]);

/**
 * Which users to prune which dead tokens from. A token shared by two
 * caregivers is *sent* once (under the first uid) but must be pruned
 * from *every* user holding it, or the second keeps it forever.
 */
export function pruneByUid(
  outcomes: SendOutcome[],
  users: Readonly<Record<string, UserDoc | undefined>>,
): Map<string, string[]> {
  const dead = new Set(
    outcomes
      .filter((o) => !o.ok && o.errorCode !== undefined && DEAD_TOKEN_CODES.has(o.errorCode))
      .map((o) => o.token),
  );
  const out = new Map<string, string[]>();
  if (dead.size === 0) return out;
  for (const [uid, user] of Object.entries(users)) {
    const tokens = Array.isArray(user?.fcmTokens) ? user.fcmTokens : [];
    const hit = [...new Set(tokens.filter((t) => dead.has(t)))];
    if (hit.length) out.set(uid, hit);
  }
  return out;
}

export interface Report {
  /** Users who should hear about this. */
  recipients: number;
  sent: number;
  failed: number;
  pruned: number;
  /** Prunes that failed (logged, not retried). */
  pruneErrors: string[];
  /** Why nothing was attempted, when that is the case. */
  skipped?: string;
}

function empty(skipped: string, recipients = 0): Report {
  return { recipients, sent: 0, failed: 0, pruned: 0, pruneErrors: [], skipped };
}

/** Send for one trigger and prune what FCM says is dead. Throws only if
 *  the read or the send fails, so a retry is worth it. */
export async function deliver(deps: Deps, t: Trigger): Promise<Report> {
  const uids = recipientUids(t);
  if (uids.length === 0) return empty("no recipients");
  const users = await deps.store.getUsers(uids);
  const notifications = buildNotifications(t, users);
  if (notifications.length === 0) return empty("no tokens", uids.length);
  const outcomes = await deps.sender.send(notifications);
  const prune = [...pruneByUid(outcomes, users)];
  const results = await Promise.allSettled(prune.map(([uid, tokens]) => deps.store.removeTokens(uid, tokens)));
  let pruned = 0;
  const pruneErrors: string[] = [];
  results.forEach((r, i) => {
    const [uid, tokens] = prune[i];
    if (r.status === "fulfilled") pruned += tokens.length;
    else pruneErrors.push(`${uid}: ${String(r.reason)}`);
  });
  const sent = outcomes.filter((o) => o.ok).length;
  return { recipients: uids.length, sent, failed: outcomes.length - sent, pruned, pruneErrors };
}

/**
 * Retries are on, so a trigger whose send keeps failing is retried for up
 * to 7 days by default. A "didn't take her medicine" push a day late is
 * noise, not help, so give up after this.
 */
export const MAX_EVENT_AGE_MS = 15 * 60 * 1000;

export function tooOld(eventTime: string | undefined, now: number, maxMs = MAX_EVENT_AGE_MS): boolean {
  if (!eventTime) return false;
  const t = Date.parse(eventTime);
  return Number.isFinite(t) && now - t > maxMs;
}

/** `null` means the trigger is routine and was dropped before any read
 *  (not worth a log line); a `Report` means it mattered. */
export type Result = Report | null;

export async function handleEventCreated(
  deps: Deps,
  deviceId: string,
  eventId: string,
  event: EventDoc | undefined,
): Promise<Result> {
  if (!event || !isNotifiableEvent(event)) return null;
  const device = await deps.store.getDevice(deviceId);
  if (!device) return empty("device not found");
  return deliver(deps, { type: "missed", deviceId, eventId, device, event });
}

export async function handleReplyCreated(
  deps: Deps,
  deviceId: string,
  msgId: string,
  replyId: string,
  reply: ReplyDoc | undefined,
): Promise<Result> {
  if (!reply) return null;
  const [device, message] = await Promise.all([
    deps.store.getDevice(deviceId),
    deps.store.getMessage(deviceId, msgId),
  ]);
  if (!device) return empty("device not found");
  if (!message) return empty("message not found");
  return deliver(deps, { type: "reply", deviceId, msgId, replyId, device, message, reply });
}

export async function handleMessageUpdated(
  deps: Deps,
  deviceId: string,
  msgId: string,
  before: MessageDoc | undefined,
  after: MessageDoc | undefined,
): Promise<Result> {
  if (!after || !spokenNow(before, after)) return null;
  const device = await deps.store.getDevice(deviceId);
  if (!device) return empty("device not found");
  return deliver(deps, { type: "spoken", deviceId, msgId, device, message: after });
}
