/**
 * Why this exists: who is told and in what words is what her family
 * experiences; these pin it down without Firestore or FCM.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  buildNotifications,
  clip,
  DeviceDoc,
  MAX_NAME,
  MAX_TEXT,
  notificationBody,
  recipientUids,
  spokenNow,
  TITLE,
  Trigger,
  UserDoc,
} from "../../src/notify";

const device: DeviceDoc = { name: "Amma", caregivers: ["cg1", "cg2"], deviceUid: "dev-uid" };
const users: Record<string, UserDoc> = {
  cg1: { name: "Ravi", fcmTokens: ["a1", "a2"] },
  cg2: { name: "Sita", fcmTokens: ["b1"] },
  outsider: { name: "X", fcmTokens: ["x1"] },
};

const missed = (text = "Take the BP tablet", kind = "missed"): Trigger => ({
  type: "missed",
  deviceId: "d1",
  eventId: "e1",
  device,
  event: { kind, text, reminderId: "r1" },
});

test("missed event goes to every token of every caregiver", () => {
  const out = buildNotifications(missed(), users);
  assert.deepEqual(
    out.map((n) => [n.uid, n.token]),
    [
      ["cg1", "a1"],
      ["cg1", "a2"],
      ["cg2", "b1"],
    ],
  );
  for (const n of out) {
    assert.equal(n.title, TITLE);
    assert.equal(n.body, "Amma didn't confirm: Take the BP tablet");
    assert.deepEqual(n.data, { type: "missed", deviceId: "d1", eventId: "e1" });
  }
});

test("non-missed events notify nobody", () => {
  for (const kind of ["fired", "acked", "timer_done", "message_spoken", "", "MISSED"]) {
    assert.deepEqual(recipientUids(missed("x", kind)), [], kind);
    assert.deepEqual(buildNotifications(missed("x", kind), users), [], kind);
  }
});

test("users without a doc or tokens are skipped, not an error", () => {
  const out = buildNotifications(missed(), { cg1: undefined, cg2: { name: "Sita" } });
  assert.deepEqual(out, []);
});

test("a token shared by two caregivers is sent once", () => {
  const out = buildNotifications(missed(), {
    cg1: { fcmTokens: ["shared", "a1"] },
    cg2: { fcmTokens: ["shared", "", "b1"] },
  });
  assert.deepEqual(
    out.map((n) => n.token),
    ["shared", "a1", "b1"],
  );
});

test("duplicate caregiver uids do not double-send", () => {
  const t: Trigger = { ...missed(), device: { ...device, caregivers: ["cg1", "cg1"] } } as Trigger;
  assert.deepEqual(recipientUids(t), ["cg1"]);
});

test("device with no caregivers field notifies nobody", () => {
  const t = { ...missed(), device: { name: "Amma" } } as Trigger;
  assert.deepEqual(buildNotifications(t, users), []);
});

test("unnamed device and empty text still read as a sentence", () => {
  const t = { ...missed(""), device: { caregivers: ["cg1"] } } as Trigger;
  assert.equal(notificationBody(t), "Saathi didn't confirm a reminder");
});

test("long text is clipped to fit an FCM payload", () => {
  const body = notificationBody(missed("word ".repeat(200)));
  const text = body.slice("Amma didn't confirm: ".length);
  assert.equal(text.length, MAX_TEXT);
  assert.ok(text.endsWith("…"));
  assert.equal(clip("  two\n\nlines  "), "two lines");
});

test("clipping never splits an emoji", () => {
  const out = clip("a".repeat(MAX_TEXT - 2) + "😀😀😀");
  assert.equal(Array.from(out).length, MAX_TEXT);
  assert.ok(out.endsWith("😀…"));
  assert.ok(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(out), "no lone high surrogate");
});

test("an over-long device name is clipped too", () => {
  const t = { ...missed("Tea"), device: { name: "N".repeat(5000), caregivers: ["cg1"] } } as Trigger;
  const body = notificationBody(t);
  assert.ok(body.startsWith("N".repeat(MAX_NAME - 1) + "… didn't confirm: Tea"));
});

const reply = (fromUid: string, text = "Yes, rice and dal"): Trigger => ({
  type: "reply",
  deviceId: "d1",
  msgId: "m1",
  replyId: "r1",
  device,
  message: { from: "Ravi", fromUid, text: "Did you eat?" },
  reply: { text },
});

test("reply goes to the message's sender only", () => {
  const out = buildNotifications(reply("cg1"), users);
  assert.deepEqual(
    out.map((n) => [n.uid, n.token, n.body]),
    [
      ["cg1", "a1", "Amma replied: Yes, rice and dal"],
      ["cg1", "a2", "Amma replied: Yes, rice and dal"],
    ],
  );
  assert.deepEqual(out[0].data, { type: "reply", deviceId: "d1", msgId: "m1", replyId: "r1" });
});

test("reply to a sender who is no longer a caregiver notifies nobody", () => {
  assert.deepEqual(buildNotifications(reply("outsider"), users), []);
  assert.deepEqual(buildNotifications(reply(""), users), []);
});

test("empty reply text still tells the sender she replied", () => {
  assert.equal(notificationBody(reply("cg1", "  ")), "Amma replied to your message");
});

test("spoken notifies the sender with the agreed sentence", () => {
  const t: Trigger = { type: "spoken", deviceId: "d1", msgId: "m1", device, message: { fromUid: "cg2" } };
  const out = buildNotifications(t, users);
  assert.deepEqual(
    out.map((n) => [n.uid, n.token, n.body]),
    [["cg2", "b1", "Amma heard your message"]],
  );
});

test("spokenNow is true only when spokenAt is first set", () => {
  assert.equal(spokenNow({}, { spokenAt: 1 }), true);
  assert.equal(spokenNow({ spokenAt: null }, { spokenAt: 1 }), true);
  assert.equal(spokenNow(undefined, { spokenAt: 1 }), true);
  assert.equal(spokenNow({ spokenAt: 1 }, { spokenAt: 2 }), false);
  assert.equal(spokenNow({}, {}), false);
  assert.equal(spokenNow({}, { spokenAt: null }), false);
  assert.equal(spokenNow({ spokenAt: 1 }, {}), false);
});
