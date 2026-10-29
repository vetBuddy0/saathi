/**
 * Why this exists: the read -> send -> prune path, driven through fake
 * `Store` and `Sender`, so pruning and early exits are proven without
 * an emulator.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  Deps,
  handleEventCreated,
  handleMessageUpdated,
  handleReplyCreated,
  MAX_EVENT_AGE_MS,
  pruneByUid,
  Report,
  Result,
  SendOutcome,
  Store,
  tooOld,
} from "../../src/deliver";
import type { DeviceDoc, MessageDoc, Notification, UserDoc } from "../../src/notify";
import { fcmSender, FCM_BATCH, selectSenderKind, toMessage } from "../../src/senders";

class FakeStore implements Store {
  reads: string[] = [];
  removed: Array<[string, string[]]> = [];
  constructor(
    public devices: Record<string, DeviceDoc> = {},
    public messages: Record<string, MessageDoc> = {},
    public users: Record<string, UserDoc> = {},
  ) {}
  async getDevice(id: string) {
    this.reads.push(`device:${id}`);
    return this.devices[id];
  }
  async getMessage(id: string, msgId: string) {
    this.reads.push(`message:${id}/${msgId}`);
    return this.messages[`${id}/${msgId}`];
  }
  async getUsers(uids: string[]) {
    this.reads.push(`users:${uids.join(",")}`);
    return Object.fromEntries(uids.map((u) => [u, this.users[u]]));
  }
  async removeTokens(uid: string, tokens: string[]) {
    this.removed.push([uid, tokens]);
  }
}

/** Records sends; tokens listed in `dead` fail with the given code. */
class FakeSender {
  sent: Notification[] = [];
  constructor(public dead: Record<string, string> = {}) {}
  async send(ns: Notification[]): Promise<SendOutcome[]> {
    this.sent.push(...ns);
    return ns.map((n) =>
      this.dead[n.token]
        ? { uid: n.uid, token: n.token, ok: false, errorCode: this.dead[n.token] }
        : { uid: n.uid, token: n.token, ok: true },
    );
  }
}

function counts(r: Result): Pick<Report, "recipients" | "sent" | "failed" | "pruned"> {
  assert.ok(r);
  return { recipients: r.recipients, sent: r.sent, failed: r.failed, pruned: r.pruned };
}

function setup(dead: Record<string, string> = {}) {
  const store = new FakeStore(
    { d1: { name: "Amma", caregivers: ["cg1", "cg2"] } },
    { "d1/m1": { from: "Ravi", fromUid: "cg1", text: "Did you eat?" } },
    {
      cg1: { fcmTokens: ["a1", "a2"] },
      cg2: { fcmTokens: ["b1", "b2"] },
    },
  );
  const sender = new FakeSender(dead);
  const deps: Deps = { store, sender };
  return { store, sender, deps };
}

test("missed event sends to all caregiver tokens", async () => {
  const { sender, deps, store } = setup();
  const r = await handleEventCreated(deps, "d1", "e1", { kind: "missed", text: "Tea" });
  assert.deepEqual(counts(r), { recipients: 2, sent: 4, failed: 0, pruned: 0 });
  assert.deepEqual(
    sender.sent.map((n) => n.token),
    ["a1", "a2", "b1", "b2"],
  );
  assert.ok(sender.sent.every((n) => n.body === "Amma didn't confirm: Tea"));
  assert.deepEqual(store.removed, []);
});

test("routine events are dropped before any read", async () => {
  const { sender, deps, store } = setup();
  for (const kind of ["fired", "acked", "timer_done", "message_spoken"]) {
    assert.equal(await handleEventCreated(deps, "d1", "e1", { kind, text: "x" }), null);
  }
  assert.equal(await handleEventCreated(deps, "d1", "e1", undefined), null);
  assert.deepEqual(store.reads, []);
  assert.deepEqual(sender.sent, []);
});

test("missed event on an unknown device sends nothing", async () => {
  const { sender, deps } = setup();
  const r = await handleEventCreated(deps, "nope", "e1", { kind: "missed", text: "Tea" });
  assert.equal(r?.skipped, "device not found");
  assert.equal(r?.sent, 0);
  assert.deepEqual(sender.sent, []);
});

test("not-registered tokens are pruned from their own user only", async () => {
  const { deps, store } = setup({
    a2: "messaging/registration-token-not-registered",
    b1: "messaging/invalid-registration-token",
    b2: "messaging/internal-error",
  });
  const r = await handleEventCreated(deps, "d1", "e1", { kind: "missed", text: "Tea" });
  assert.deepEqual(counts(r), { recipients: 2, sent: 1, failed: 3, pruned: 2 });
  assert.deepEqual(store.removed, [
    ["cg1", ["a2"]],
    ["cg2", ["b1"]],
  ]);
});

test("transient and payload errors never prune", () => {
  const dead = pruneByUid(
    [
      { uid: "u", token: "t1", ok: false, errorCode: "messaging/quota-exceeded" },
      { uid: "u", token: "t2", ok: false, errorCode: "messaging/unavailable" },
      { uid: "u", token: "t3", ok: false, errorCode: "messaging/invalid-argument" },
      { uid: "u", token: "t4", ok: false },
      { uid: "u", token: "t5", ok: true },
    ],
    { u: { fcmTokens: ["t1", "t2", "t3", "t4", "t5"] } },
  );
  assert.equal(dead.size, 0);
});

test("a dead token shared by two caregivers is pruned from both", async () => {
  const store = new FakeStore(
    { d1: { name: "Amma", caregivers: ["cg1", "cg2"] } },
    {},
    { cg1: { fcmTokens: ["shared", "a1"] }, cg2: { fcmTokens: ["b1", "shared"] } },
  );
  const sender = new FakeSender({ shared: "messaging/registration-token-not-registered" });
  const r = await handleEventCreated({ store, sender }, "d1", "e1", { kind: "missed", text: "Tea" });
  assert.equal(sender.sent.filter((n) => n.token === "shared").length, 1);
  assert.deepEqual(store.removed, [
    ["cg1", ["shared"]],
    ["cg2", ["shared"]],
  ]);
  assert.equal(r?.pruned, 2);
});

test("a failed prune is reported, not thrown (a retry would re-send everything)", async () => {
  const { deps, store } = setup({ a1: "messaging/registration-token-not-registered" });
  store.removeTokens = async () => {
    throw new Error("firestore down");
  };
  const r = await handleEventCreated(deps, "d1", "e1", { kind: "missed", text: "Tea" });
  assert.equal(r?.sent, 3);
  assert.equal(r?.pruned, 0);
  assert.equal(r?.pruneErrors.length, 1);
});

test("a failed send throws so the trigger is retried", async () => {
  const { deps } = setup();
  deps.sender.send = async () => {
    throw new Error("fcm 503");
  };
  await assert.rejects(handleEventCreated(deps, "d1", "e1", { kind: "missed", text: "Tea" }), /fcm 503/);
});

test("a missed event that reaches no phone still reports why", async () => {
  const store = new FakeStore({ d1: { name: "Amma", caregivers: ["cg1"] } }, {}, { cg1: { name: "Ravi" } });
  const r = await handleEventCreated({ store, sender: new FakeSender() }, "d1", "e1", { kind: "missed" });
  assert.deepEqual({ skipped: r?.skipped, recipients: r?.recipients }, { skipped: "no tokens", recipients: 1 });
});

test("events older than the retry window are given up on", () => {
  const now = Date.parse("2026-10-29T10:00:00Z");
  assert.equal(tooOld("2026-10-29T09:59:00Z", now), false);
  assert.equal(tooOld(new Date(now - MAX_EVENT_AGE_MS - 1).toISOString(), now), true);
  assert.equal(tooOld(undefined, now), false);
  assert.equal(tooOld("not a date", now), false);
});

test("reply notifies only the original sender", async () => {
  const { sender, deps } = setup();
  const r = await handleReplyCreated(deps, "d1", "m1", "r1", { text: "Yes" });
  assert.equal(r?.sent, 2);
  assert.deepEqual(
    sender.sent.map((n) => [n.uid, n.token, n.body]),
    [
      ["cg1", "a1", "Amma replied: Yes"],
      ["cg1", "a2", "Amma replied: Yes"],
    ],
  );
});

test("reply to a missing message sends nothing", async () => {
  const { sender, deps } = setup();
  const r = await handleReplyCreated(deps, "d1", "gone", "r1", { text: "Yes" });
  assert.equal(r?.skipped, "message not found");
  assert.deepEqual(sender.sent, []);
});

test("spokenAt first set notifies the sender once", async () => {
  const { sender, deps } = setup();
  const before = { fromUid: "cg1", text: "hi" };
  const after = { fromUid: "cg1", text: "hi", spokenAt: 123 };
  await handleMessageUpdated(deps, "d1", "m1", before, after);
  await handleMessageUpdated(deps, "d1", "m1", after, { ...after, spokenAt: 456 });
  await handleMessageUpdated(deps, "d1", "m1", before, { ...before, text: "edited" });
  assert.deepEqual(
    sender.sent.map((n) => [n.uid, n.body]),
    [
      ["cg1", "Amma heard your message"],
      ["cg1", "Amma heard your message"],
    ],
  );
});

test("fcmSender batches at 500 and maps per-token errors", async () => {
  const calls: number[] = [];
  const messaging = {
    async sendEach(msgs: Array<{ token?: string }>) {
      calls.push(msgs.length);
      return {
        successCount: 0,
        failureCount: 0,
        responses: msgs.map((m) =>
          m.token === "t3"
            ? { success: false, error: { code: "messaging/registration-token-not-registered" } }
            : { success: true, messageId: "x" },
        ),
      };
    },
  };
  const ns: Notification[] = Array.from({ length: FCM_BATCH + 2 }, (_, i) => ({
    uid: "u",
    token: `t${i}`,
    title: "Saathi",
    body: "b",
    data: {},
  }));
  const out = await fcmSender(messaging as never).send(ns);
  assert.deepEqual(calls, [FCM_BATCH, 2]);
  assert.equal(out.length, ns.length);
  assert.deepEqual(out[3], {
    uid: "u",
    token: "t3",
    ok: false,
    errorCode: "messaging/registration-token-not-registered",
  });
  assert.equal(out.filter((o) => o.ok).length, ns.length - 1);
});

test("toMessage carries the notification and data to FCM", () => {
  const m = toMessage({ uid: "u", token: "t", title: "Saathi", body: "b", data: { type: "missed" } });
  assert.deepEqual(
    { token: (m as { token: string }).token, notification: m.notification, data: m.data },
    { token: "t", notification: { title: "Saathi", body: "b" }, data: { type: "missed" } },
  );
});

test("outbox needs the flag and the emulator", () => {
  assert.deepEqual(selectSenderKind({}), { kind: "fcm" });
  assert.deepEqual(selectSenderKind({ FUNCTIONS_EMULATOR: "true" }), { kind: "fcm" });
  assert.deepEqual(selectSenderKind({ SAATHI_FCM_OUTBOX: "1", FUNCTIONS_EMULATOR: "true" }), {
    kind: "outbox",
  });
  const prod = selectSenderKind({ SAATHI_FCM_OUTBOX: "1" });
  assert.equal(prod.kind, "fcm");
  assert.ok(prod.warning);
});
