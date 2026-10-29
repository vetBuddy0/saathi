/**
 * Why this exists: FCM cannot be reached from the emulator, but the e2e
 * test still has to prove the triggers fire and pick the right phones.
 * So the sender is swappable: the real one calls FCM, the outbox one
 * writes each intended send to `_test_outbox` for the test to read.
 *
 * Contested: the outbox needs *both* `SAATHI_FCM_OUTBOX=1` and the
 * emulator's own `FUNCTIONS_EMULATOR=true`. Lost: the flag alone. A flag
 * copied into a production `.env` would make every missed-medicine alert
 * vanish into a collection nobody reads; requiring the emulator makes
 * that impossible, and the flag is ignored (with a warning) in prod.
 */

import type { Firestore } from "firebase-admin/firestore";
import type { Messaging, Message } from "firebase-admin/messaging";
import type { Notification } from "./notify";
import type { Sender, SendOutcome } from "./deliver";

/** FCM's `sendEach` accepts at most this many messages per call. */
export const FCM_BATCH = 500;

export const OUTBOX = "_test_outbox";

/** In outbox mode, tokens with this prefix report as dead so the e2e can
 *  exercise pruning without real FCM. */
export const OUTBOX_DEAD_PREFIX = "test-unregistered-";

export type SenderKind = "fcm" | "outbox";

export function selectSenderKind(env: Record<string, string | undefined>): {
  kind: SenderKind;
  warning?: string;
} {
  const wantsOutbox = env.SAATHI_FCM_OUTBOX === "1";
  const inEmulator = env.FUNCTIONS_EMULATOR === "true";
  if (wantsOutbox && inEmulator) return { kind: "outbox" };
  if (wantsOutbox) {
    return {
      kind: "fcm",
      warning: "SAATHI_FCM_OUTBOX is set outside the emulator; ignoring it and sending via FCM.",
    };
  }
  return { kind: "fcm" };
}

export function toMessage(n: Notification): Message {
  return {
    token: n.token,
    notification: { title: n.title, body: n.body },
    data: n.data,
    // A missed reminder should wake the phone, not wait for a batch window.
    android: { priority: "high" },
    apns: { headers: { "apns-priority": "10" } },
  };
}

export function fcmSender(messaging: Pick<Messaging, "sendEach">): Sender {
  return {
    async send(notifications: Notification[]): Promise<SendOutcome[]> {
      const outcomes: SendOutcome[] = [];
      for (let i = 0; i < notifications.length; i += FCM_BATCH) {
        const chunk = notifications.slice(i, i + FCM_BATCH);
        const res = await messaging.sendEach(chunk.map(toMessage));
        res.responses.forEach((r, j) => {
          const n = chunk[j];
          outcomes.push(
            r.success
              ? { uid: n.uid, token: n.token, ok: true }
              : { uid: n.uid, token: n.token, ok: false, errorCode: r.error?.code },
          );
        });
      }
      return outcomes;
    },
  };
}

export function outboxSender(db: Firestore): Sender {
  return {
    async send(notifications: Notification[]): Promise<SendOutcome[]> {
      const outcomes: SendOutcome[] = [];
      // A WriteBatch holds at most 500 writes, the same cap as sendEach.
      for (let i = 0; i < notifications.length; i += FCM_BATCH) {
        const batch = db.batch();
        for (const n of notifications.slice(i, i + FCM_BATCH)) {
          const dead = n.token.startsWith(OUTBOX_DEAD_PREFIX);
          const outcome: SendOutcome = dead
            ? { uid: n.uid, token: n.token, ok: false, errorCode: "messaging/registration-token-not-registered" }
            : { uid: n.uid, token: n.token, ok: true };
          batch.set(db.collection(OUTBOX).doc(), {
            ...n,
            ok: outcome.ok,
            errorCode: outcome.errorCode ?? null,
            at: new Date(),
          });
          outcomes.push(outcome);
        }
        await batch.commit();
      }
      return outcomes;
    },
  };
}
