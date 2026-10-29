/**
 * Why this exists: unit tests prove the logic, not that the triggers are
 * bound to the right paths and actually run. This drives the real
 * functions in the emulator through Firestore writes, the same way the
 * device will, and reads what they meant to send from `_test_outbox`
 * (FCM is unreachable from the emulator; see senders.ts).
 *
 * Run via `npm run e2e` (builds, then `firebase emulators:exec`).
 */

import { after, before, test } from "node:test";
import assert from "node:assert/strict";

import { deleteApp, initializeApp, App } from "firebase-admin/app";
import { Firestore, getFirestore } from "firebase-admin/firestore";

const PROJECT = "demo-saathi";
const OUTBOX = "_test_outbox";

let app: App;
let db: Firestore;

interface OutboxDoc {
  uid: string;
  token: string;
  title: string;
  body: string;
  data: Record<string, string>;
  ok: boolean;
}

async function waitFor<T>(what: string, fn: () => Promise<T | undefined>, ms = 30_000): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v !== undefined) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

async function outbox(deviceId: string, type: string): Promise<OutboxDoc[]> {
  const snap = await db.collection(OUTBOX).where("data.deviceId", "==", deviceId).get();
  return snap.docs.map((d) => d.data() as OutboxDoc).filter((d) => d.data.type === type);
}

/** Waits until exactly `n` sends of `type` exist, then a moment longer to
 *  catch any extra (duplicate or wrong-recipient) send arriving late. */
async function settle(deviceId: string, type: string, n: number): Promise<OutboxDoc[]> {
  await waitFor(`${n} ${type} sends`, async () => {
    const docs = await outbox(deviceId, type);
    return docs.length >= n ? docs : undefined;
  });
  await new Promise((r) => setTimeout(r, 1500));
  return outbox(deviceId, type);
}

const sorted = (docs: OutboxDoc[]) => docs.map((d) => [d.uid, d.token, d.body]).sort();

const deviceId = `dev-${Date.now()}`;

before(async () => {
  assert.ok(
    process.env.FIRESTORE_EMULATOR_HOST,
    "FIRESTORE_EMULATOR_HOST is not set: run this through `npm run e2e`, never against a real project",
  );
  app = initializeApp({ projectId: PROJECT }, "e2e");
  db = getFirestore(app);

  await db.doc("users/cg1").set({ name: "Ravi", fcmTokens: ["tok-a1", "tok-a2"] });
  await db.doc("users/cg2").set({ name: "Sita", fcmTokens: ["tok-b1", "test-unregistered-b2"] });
  await db.doc("users/outsider").set({ name: "Not family", fcmTokens: ["tok-x"] });
  await db.doc(`devices/${deviceId}`).set({
    name: "Amma",
    createdAt: new Date(),
    caregivers: ["cg1", "cg2"],
    deviceUid: "device-uid-1",
  });
});

after(async () => {
  if (app) await deleteApp(app);
});

test("a missed event notifies every caregiver token and prunes the dead one", async () => {
  const events = db.collection(`devices/${deviceId}/events`);
  await events.doc("fired-1").set({ ts: new Date(), kind: "fired", reminderId: "r1", text: "BP tablet" });
  await events.doc("missed-1").set({ ts: new Date(), kind: "missed", reminderId: "r1", text: "BP tablet" });

  const sends = await settle(deviceId, "missed", 4);
  assert.deepEqual(sorted(sends), [
    ["cg1", "tok-a1", "Amma didn't confirm: BP tablet"],
    ["cg1", "tok-a2", "Amma didn't confirm: BP tablet"],
    ["cg2", "test-unregistered-b2", "Amma didn't confirm: BP tablet"],
    ["cg2", "tok-b1", "Amma didn't confirm: BP tablet"],
  ]);
  assert.ok(sends.every((d) => d.data.eventId === "missed-1"), "the fired event must not notify");
  assert.ok(sends.every((d) => d.title === "Saathi"));

  const cg2 = await waitFor("dead token pruned", async () => {
    const tokens = (await db.doc("users/cg2").get()).get("fcmTokens") as string[];
    return tokens.includes("test-unregistered-b2") ? undefined : tokens;
  });
  assert.deepEqual(cg2, ["tok-b1"]);
  assert.deepEqual((await db.doc("users/cg1").get()).get("fcmTokens"), ["tok-a1", "tok-a2"]);
});

test("a reply notifies only the message's sender", async () => {
  const msg = db.doc(`devices/${deviceId}/inbox/msg-1`);
  await msg.set({ from: "Ravi", fromUid: "cg1", text: "Did you eat?", createdAt: new Date() });
  await msg.collection("replies").doc("reply-1").set({ ts: new Date(), text: "Yes, rice and dal" });

  const sends = await settle(deviceId, "reply", 2);
  assert.deepEqual(sorted(sends), [
    ["cg1", "tok-a1", "Amma replied: Yes, rice and dal"],
    ["cg1", "tok-a2", "Amma replied: Yes, rice and dal"],
  ]);
  assert.ok(sends.every((d) => d.data.msgId === "msg-1" && d.data.replyId === "reply-1"));
});

test("spokenAt notifies the sender once, when first set", async () => {
  const msg = db.doc(`devices/${deviceId}/inbox/msg-2`);
  await msg.set({ from: "Sita", fromUid: "cg2", text: "Call me", createdAt: new Date() });
  await msg.update({ spokenAt: new Date() });

  const first = await settle(deviceId, "spoken", 1);
  assert.deepEqual(sorted(first), [["cg2", "tok-b1", "Amma heard your message"]]);

  await msg.update({ spokenAt: new Date(Date.now() + 1000) });
  await new Promise((r) => setTimeout(r, 2500));
  assert.equal((await outbox(deviceId, "spoken")).length, 1, "re-setting spokenAt must not notify again");
});
