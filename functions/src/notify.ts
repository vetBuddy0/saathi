/**
 * Why this exists: who hears about what, and in which words, is the part
 * of push that matters to her family and the part that is easy to get
 * wrong (the wrong caregiver told, a removed caregiver still told). It is
 * kept here as pure functions over plain documents so it is tested without
 * Firestore or FCM; `index.ts` only wires these to triggers.
 *
 * Contested:
 * - The full sentence goes in the notification *body* and the title is
 *   just "Saathi". Lost: a short title ("Amma didn't confirm") with the
 *   reminder text as body. The agreed wording is one sentence and Android
 *   truncates titles to one line, so the sentence lives where it fits.
 * - Replies and "heard your message" go to the sender only while the
 *   sender is still one of the device's caregivers. Lost: notifying
 *   `fromUid` unconditionally. A caregiver removed from the device should
 *   stop receiving her words, even for a message sent before removal.
 */

/** Documents as the shared contract (`contracts/`) defines them. Every
 *  field is optional here because Firestore does not enforce a schema;
 *  the code below must survive a half-written document. */
export interface DeviceDoc {
  name?: string;
  caregivers?: string[];
  deviceUid?: string;
}

export interface UserDoc {
  name?: string;
  fcmTokens?: string[];
}

export interface EventDoc {
  kind?: string;
  text?: string;
  reminderId?: string;
}

export interface MessageDoc {
  from?: string;
  fromUid?: string;
  text?: string;
  spokenAt?: unknown;
}

export interface ReplyDoc {
  text?: string;
}

/** What happened, with everything needed to decide who to tell. */
export type Trigger =
  | { type: "missed"; deviceId: string; eventId: string; device: DeviceDoc; event: EventDoc }
  | {
      type: "reply";
      deviceId: string;
      msgId: string;
      replyId: string;
      device: DeviceDoc;
      message: MessageDoc;
      reply: ReplyDoc;
    }
  | { type: "spoken"; deviceId: string; msgId: string; device: DeviceDoc; message: MessageDoc };

/** One push to one phone. `uid` is kept so a dead token can be pruned
 *  from the right user. */
export interface Notification {
  uid: string;
  token: string;
  title: string;
  body: string;
  data: Record<string, string>;
}

export const TITLE = "Saathi";

/** Used when the device has no name yet (pairing before naming). */
export const FALLBACK_DEVICE_NAME = "Saathi";

/** FCM payloads are capped at 4 KB; a reminder or reply longer than this
 *  is clipped rather than letting the whole send fail. */
export const MAX_TEXT = 200;

/** A device name is a name; anything longer is a mistake, clipped so it
 *  cannot push every payload for that device over the FCM limit. */
export const MAX_NAME = 60;

/** Clips by code point, never splitting an emoji's surrogate pair. */
export function clip(text: string, max = MAX_TEXT): string {
  const chars = Array.from(text.trim().replace(/\s+/g, " "));
  return chars.length <= max ? chars.join("") : chars.slice(0, max - 1).join("").trimEnd() + "…";
}

function deviceName(device: DeviceDoc): string {
  const n = typeof device.name === "string" ? clip(device.name, MAX_NAME) : "";
  return n || FALLBACK_DEVICE_NAME;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function caregiversOf(device: DeviceDoc): string[] {
  const list = Array.isArray(device.caregivers) ? device.caregivers : [];
  return [...new Set(list.filter((u): u is string => typeof u === "string" && u !== ""))];
}

/** Only these event kinds reach a phone. `fired`/`acked` are routine and
 *  the caregiver app shows them on its own timeline. */
export function isNotifiableEvent(event: EventDoc | undefined): boolean {
  return event?.kind === "missed";
}

/** True only for the update that first sets `spokenAt`, so re-writes of
 *  the field (or any other edit) do not notify twice. */
export function spokenNow(before: MessageDoc | undefined, after: MessageDoc | undefined): boolean {
  const was = before?.spokenAt;
  const is = after?.spokenAt;
  return (was === undefined || was === null) && is !== undefined && is !== null;
}

/** The uids that should hear about this trigger. */
export function recipientUids(t: Trigger): string[] {
  const caregivers = caregiversOf(t.device);
  switch (t.type) {
    case "missed":
      return isNotifiableEvent(t.event) ? caregivers : [];
    case "reply":
    case "spoken": {
      const from = str(t.message.fromUid);
      return from && caregivers.includes(from) ? [from] : [];
    }
  }
}

/** The sentence for this trigger. */
export function notificationBody(t: Trigger): string {
  const name = deviceName(t.device);
  switch (t.type) {
    case "missed": {
      const text = clip(str(t.event.text));
      return text ? `${name} didn't confirm: ${text}` : `${name} didn't confirm a reminder`;
    }
    case "reply": {
      const text = clip(str(t.reply.text));
      return text ? `${name} replied: ${text}` : `${name} replied to your message`;
    }
    case "spoken":
      return `${name} heard your message`;
  }
}

/** Data for the caregiver app to open the right screen. FCM data values
 *  must be strings. */
function dataFor(t: Trigger): Record<string, string> {
  switch (t.type) {
    case "missed":
      return { type: t.type, deviceId: t.deviceId, eventId: t.eventId };
    case "reply":
      return { type: t.type, deviceId: t.deviceId, msgId: t.msgId, replyId: t.replyId };
    case "spoken":
      return { type: t.type, deviceId: t.deviceId, msgId: t.msgId };
  }
}

/**
 * One notification per (recipient, token). A token shared by two
 * caregivers (one phone, two logins) is sent to once.
 *
 * `users` holds the user documents for `recipientUids(t)`; a missing or
 * token-less user is skipped, not an error — they have not opened the
 * app on a phone yet.
 */
export function buildNotifications(
  t: Trigger,
  users: Readonly<Record<string, UserDoc | undefined>>,
): Notification[] {
  const uids = recipientUids(t);
  if (uids.length === 0) return [];
  const body = notificationBody(t);
  const data = dataFor(t);
  const seen = new Set<string>();
  const out: Notification[] = [];
  for (const uid of uids) {
    const tokens = users[uid]?.fcmTokens;
    if (!Array.isArray(tokens)) continue;
    for (const token of tokens) {
      if (typeof token !== "string" || token === "" || seen.has(token)) continue;
      seen.add(token);
      out.push({ uid, token, title: TITLE, body, data: { ...data } });
    }
  }
  return out;
}
