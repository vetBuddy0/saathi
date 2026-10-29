# contracts/

The shared contract between Saathi's tablet, the caregiver app and the
Cloud Functions: what each Firestore document looks like, where audio
and memory live in Storage, and who may read or write each one.

## Why it lives here

The device is Python (`saathi/`). The caregiver app is TypeScript
(`apps/caregiver`), and so are the Cloud Functions (`functions/`). They
meet only in Firebase, so the contract between them has to live
somewhere both can read. Keeping it in this repo, in its own folder,
gives us two things:

- **One PR changes both ends.** A new field is a change to a schema, a
  sample, the rules and their tests, all in one diff. The device side and
  the app side are reviewed against it together.
- **Both sides' tests read the same files.**
  - `tests/test_contracts.py` (pytest) checks each sample against its
    schema.
  - `test/*.test.mjs` (mocha, against the Firestore and Storage
    emulators) writes the same samples through the rules.
  - A schema, a sample and a rule can't drift apart without a red test.

Lost: a separate npm package or repo for the contract. That needs
publishing and versioning, and lets the device and the app pin different
versions of it: the drift this folder exists to prevent.

## Timestamps

In Firestore every time field is a **Firestore Timestamp**: `createdAt`,
`ts`, `spokenAt`, `updatedAt` and `expiresAt`. The rules check `is
timestamp`, so writing a string is denied.

The JSON files here can't hold a Timestamp. In `schemas/` and `samples/`
a time is an **RFC 3339 string** (`"2026-10-29T09:30:05Z"`) with
`"format": "date-time"`. The rules tests turn each such field into a
Timestamp before writing it (`test/helpers.mjs`, `sample()`).

`reminders.time` (`"HH:MM"`) and `reminders.date` (`"YYYY-MM-DD"`) are
not timestamps. They are plain strings in the device's local time
everywhere.

## The shapes

All device data sits under `devices/{deviceId}`. `?` marks an optional
field.

| Path | Fields | Written by | Read by |
|---|---|---|---|
| `devices/{id}` | `name, createdAt, caregivers: [uid], deviceUid, pairingCode?` | the device (creates it); caregivers (rename, leave, join by pairing) | the device, its caregivers |
| `devices/{id}/turns/{turnId}` | `ts, heard, said, language, clipPath?` | the device | the device, its caregivers |
| `devices/{id}/events/{eventId}` | `ts, kind: fired\|acked\|missed\|timer_done\|message_spoken, reminderId?, text` | the device | the device, its caregivers |
| `devices/{id}/inbox/{msgId}` | `from, fromUid, text, createdAt, spokenAt?` | caregivers create (`fromUid` = own uid, no `spokenAt`); the device sets `spokenAt` only | the device, its caregivers |
| `devices/{id}/inbox/{msgId}/replies/{replyId}` | `ts, text` | the device | the device, its caregivers |
| `devices/{id}/reminders/{rid}` | `text, time "HH:MM", date? "YYYY-MM-DD", repeat: none\|daily\|weekdays\|weekly, active, createdBy, updatedAt` | caregivers (`createdBy` = own uid, never changes); the device may only change `active` and `updatedAt` | the device, its caregivers |
| `pairing/{code}` | `deviceId, expiresAt, claimedBy?` | the device opens it; a caregiver claims it | the device; anyone signed in who holds an open code |
| `users/{uid}` | `name, fcmTokens: [..]` | that uid | that uid |

The schemas are in `schemas/<shape>.schema.json`, with one valid example
of each in `samples/<shape>.json`. The file names are `device`, `turn`,
`event`, `message`, `reply`, `reminder`, `pairing` and `user`.

**Limits the rules enforce.** These are also in the schemas:

- Names: up to 100 characters.
- Message text: 1 to 1000 characters.
- Reply text: up to 4000 characters.
- Reminder text: up to 500 characters.
- `language`: up to 20 characters.
- `caregivers` and `fcmTokens`: at most 20 entries each.

Every document is closed: a field not listed is denied. A collection not
listed is denied too, to the device as well.

### Storage

| Path | Written by | Read by |
|---|---|---|
| `devices/{id}/audio/{turnId}.wav` (< 20 MB) | the device | the device, its caregivers |
| `devices/{id}/memory/{ts}.sqlite3` (< 200 MB) | the device | the device only |

Storage rules read `devices/{id}` from Firestore for every decision.
Caregivers can't read the memory copy: it is a backup of everything she
has said, not something the app shows (see `storage.rules`).

## Who is who

- **The device** signs in with Email/Password as
  `device-{id}@saathi.invalid`, and `devices/{id}.deviceUid` is that
  user's uid. It can create `devices/{id}` only when:
  - its email matches `{id}`;
  - `deviceUid` is set to itself;
  - there are no caregivers;
  - `{id}` is **16 to 64 characters of `[A-Za-z0-9_-]`, generated at
    random on the tablet** (for example a UUID4 or 128 random bits in
    base64url).

  Email/Password sign-up is open, so whoever registers
  `device-{id}@saathi.invalid` first owns `devices/{id}`. An id nobody can
  predict is what makes that the real tablet. Never derive it from a
  serial number or a counter.

  After that the device reads and writes only `devices/{its id}/**` and
  its own `pairing` docs.
- **A caregiver** is any signed-in user whose uid is in
  `devices/{id}.caregivers`.
  - They may rename the device, or remove themselves.
  - They may never remove or add anyone else; a new caregiver joins only
    through pairing.
  - The app lists a user's devices with
    `where('caregivers', 'array-contains', uid)`. The rules allow only
    that query, not a bare list.
- **Everyone else** is denied, signed in or not.

## Pairing

1. The device creates `pairing/{code}`, where `code` is 8 random characters
   from `23456789ABCDEFGHJKMNPQRSTVWXYZ` (no 0/O, 1/I/L, U). Shown as
   `K7QM-4XHP`; stored without the dash.
   It holds `{deviceId, expiresAt}`, with `expiresAt` at most 10 minutes
   ahead (the rules allow up to 11, for the tablet's clock). If the code
   is already taken, the create is denied; pick another.
2. The caregiver types the code. The app may `get` `pairing/{code}` while
   it is open, to show which device it is for. Listing codes is never
   allowed.
3. The caregiver claims it in **one batched write**:

   ```ts
   const batch = writeBatch(db);
   batch.update(doc(db, 'pairing', code), { claimedBy: uid });
   batch.update(doc(db, 'devices', deviceId), {
     caregivers: arrayUnion(uid),
     pairingCode: code,
   });
   await batch.commit();
   ```

   The rules use `get`/`getAfter` to check both sides of the batch:
   - the code was for this device;
   - it was unexpired and unclaimed before the batch;
   - after the batch, it is claimed by this uid;
   - after the batch, this uid is on the device.

   Either write alone is denied. A claimed code can't be claimed again,
   and an old code can't be replayed to rejoin after the device removes
   someone.

`devices/{id}.pairingCode` is **the one field beyond the original
contract**. A rule on `devices/{id}` has no other way to find the pairing
doc that justifies the join. Only the claim writes it.

**Brute force (closed 2026-10-29).** Any signed-in user may `get` an
open code, anyone can sign up, and rules can't count attempts. So the
code's size is the defence: 30^8 ≈ 6.6×10¹¹ codes against a 10-minute
life. Six digits (10^6, tried in minutes) lost. So did a long token in
the QR with six digits as a typed fallback, because the fallback is
the hole. App Check is still worth adding later.

## Running the tests

The rules suite runs against the real emulators. This is the end-to-end
test for this folder. You need Node 22 and firebase-tools, plus a JDK for
the emulators:

```sh
cd contracts
npm install
firebase emulators:exec --only firestore,storage,auth --project demo-saathi "npm test"
# or: npm run test:emulators
```

`demo-saathi` is a demo project id: the emulators never touch a real
Firebase project. `firebase.json` is for the emulators only. It uses its
own ports so it can run beside other emulator suites:

- firestore: 8181 (websocket 8187)
- auth: 9191
- storage: 9291
- hub: 4401
- logging: 4501
- UI: off

The schema and sample check is plain pytest, from the repo root:

```sh
env PYTHONPATH= python -m pytest -q -p no:cacheprovider tests/test_contracts.py
```

## Changing the contract

Change the schema, the sample, the rules and the rules tests in the same
PR. Then update `CONTRACT_FIELDS` in `tests/test_contracts.py`, which pins
the field names the other units code against. If a field changes, the
device client and the app change in that PR too, or the PR says which
follow-up does it.
