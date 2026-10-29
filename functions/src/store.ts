/**
 * Why this exists: the `Store` the delivery path needs, over the Admin
 * SDK. Kept apart from `deliver.ts` so the logic never imports Firestore
 * and its unit tests never need an emulator.
 */

import { FieldValue, Firestore } from "firebase-admin/firestore";
import type { DeviceDoc, MessageDoc, UserDoc } from "./notify";
import type { Store } from "./deliver";

/** gRPC NOT_FOUND, which `update()` raises when the document is gone. */
const NOT_FOUND = 5;

export function firestoreStore(db: Firestore): Store {
  return {
    async getDevice(deviceId) {
      const snap = await db.doc(`devices/${deviceId}`).get();
      return snap.exists ? (snap.data() as DeviceDoc) : undefined;
    },
    async getMessage(deviceId, msgId) {
      const snap = await db.doc(`devices/${deviceId}/inbox/${msgId}`).get();
      return snap.exists ? (snap.data() as MessageDoc) : undefined;
    },
    async getUsers(uids) {
      const out: Record<string, UserDoc | undefined> = {};
      if (uids.length === 0) return out;
      const snaps = await db.getAll(...uids.map((u) => db.doc(`users/${u}`)));
      snaps.forEach((s, i) => {
        out[uids[i]] = s.exists ? (s.data() as UserDoc) : undefined;
      });
      return out;
    },
    async removeTokens(uid, tokens) {
      if (tokens.length === 0) return;
      try {
        await db.doc(`users/${uid}`).update({ fcmTokens: FieldValue.arrayRemove(...tokens) });
      } catch (e) {
        // Deleted between the send and now: nothing left to prune.
        if ((e as { code?: unknown }).code !== NOT_FOUND) throw e;
      }
    },
  };
}
