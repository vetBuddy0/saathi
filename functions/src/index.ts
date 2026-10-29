/**
 * Why this exists: caregivers must hear on their phones, with the app
 * closed, when she misses a reminder or answers them. The device cannot
 * send that push itself — it deliberately holds no service account — so
 * the server does it, triggered by the documents the device already
 * writes. See DECISIONS.md (2026-10-29, caregiver push) for the options
 * that lost.
 *
 * These are thin wrappers: everything decidable lives in `notify.ts` and
 * `deliver.ts`, which are unit-tested without Firestore.
 *
 * NOT DEPLOYED. Cloud Functions need the Blaze (pay-as-you-go) plan, which
 * is the owner's call and is unconfirmed. See README.md.
 */

import { initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";
import { getMessaging } from "firebase-admin/messaging";
import * as logger from "firebase-functions/logger";
import { setGlobalOptions } from "firebase-functions/v2";
import { onDocumentCreated, onDocumentUpdated } from "firebase-functions/v2/firestore";

import { Deps, handleEventCreated, handleMessageUpdated, handleReplyCreated, Result, tooOld } from "./deliver";
import type { EventDoc, MessageDoc, ReplyDoc } from "./notify";
import { fcmSender, outboxSender, selectSenderKind } from "./senders";
import { firestoreStore } from "./store";

/**
 * Must equal the Firestore database's location, which is chosen when the
 * owner creates the project (Firestore triggers cannot cross regions).
 * asia-south1 (Mumbai) assumes the database is created there, nearest
 * the device; change both together.
 */
const REGION = "asia-south1";

// A hard ceiling on concurrent instances is the cheapest guard against a
// runaway bill (e.g. a device stuck writing events in a loop).
setGlobalOptions({ region: REGION, maxInstances: 5 });

initializeApp();
const db = getFirestore();

const choice = selectSenderKind(process.env);
if (choice.warning) logger.warn(choice.warning);

const deps: Deps = {
  store: firestoreStore(db),
  sender: choice.kind === "outbox" ? outboxSender(db) : fcmSender(getMessaging()),
};

/**
 * Every notifiable trigger logs, including one that reached no phone, so
 * "we never got the alert" can be told apart from "the trigger never ran".
 */
function log(name: string, params: Record<string, string>, report: Result): void {
  if (report === null) return;
  if (report.pruneErrors.length) logger.error(`${name}: token prune failed`, { ...params, ...report });
  else if (report.sent === 0) logger.warn(`${name}: nothing delivered`, { ...params, ...report });
  else logger.info(name, { ...params, ...report });
}

/** Retried on a thrown error (a failed read or send), within MAX_EVENT_AGE_MS. */
function expired(name: string, time: string, params: Record<string, string>): boolean {
  if (!tooOld(time, Date.now())) return false;
  logger.error(`${name}: gave up, event too old to notify`, { ...params, time });
  return true;
}

export const onEventCreated = onDocumentCreated(
  { document: "devices/{deviceId}/events/{eventId}", retry: true },
  async (event) => {
    const { deviceId, eventId } = event.params;
    if (expired("onEventCreated", event.time, { deviceId, eventId })) return;
    const report = await handleEventCreated(deps, deviceId, eventId, event.data?.data() as EventDoc | undefined);
    log("onEventCreated", { deviceId, eventId }, report);
  },
);

export const onReplyCreated = onDocumentCreated(
  { document: "devices/{deviceId}/inbox/{msgId}/replies/{replyId}", retry: true },
  async (event) => {
    const { deviceId, msgId, replyId } = event.params;
    if (expired("onReplyCreated", event.time, { deviceId, msgId, replyId })) return;
    const report = await handleReplyCreated(
      deps,
      deviceId,
      msgId,
      replyId,
      event.data?.data() as ReplyDoc | undefined,
    );
    log("onReplyCreated", { deviceId, msgId, replyId }, report);
  },
);

export const onMessageSpoken = onDocumentUpdated(
  { document: "devices/{deviceId}/inbox/{msgId}", retry: true },
  async (event) => {
    const { deviceId, msgId } = event.params;
    if (expired("onMessageSpoken", event.time, { deviceId, msgId })) return;
    const report = await handleMessageUpdated(
      deps,
      deviceId,
      msgId,
      event.data?.before.data() as MessageDoc | undefined,
      event.data?.after.data() as MessageDoc | undefined,
    );
    log("onMessageSpoken", { deviceId, msgId }, report);
  },
);
