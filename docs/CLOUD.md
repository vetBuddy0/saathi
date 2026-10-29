# Cloud copy (Firebase)

Every exchange, the clip the transcriber heard, and the memory file are
copied to the owner's Firebase project (`saathi/cloud/sync.py`, over
`saathi/cloud/firebase.py`). The device stays the source of truth; the
Firebase project is what the caregiver app reads.

The device signs in as its **own** Firebase Auth user,
`device-<device-id>@saathi.invalid`, with a random password it makes
once and keeps in `<data dir>/cloud/device-credential` (mode 0600; never
logged, never shown in the console). There is no service account on the
device, ever: security rules decide what that user may touch.

## Setting it up (once)

1. [Firebase console](https://console.firebase.google.com) → **Add
   project** (e.g. `saathi-family`).
2. Build → **Authentication** → Get started → Sign-in method → enable
   **Email/Password**.
3. Build → **Firestore Database** → Create database (production mode,
   the region nearest the device, e.g. `asia-southeast1`).
4. Build → **Storage** → Get started (same region). Storage needs the
   Blaze plan on new projects.
5. Deploy the security rules from `contracts/` (`firebase deploy --only
   firestore:rules,storage`). They arrive with the pairing work, keyed
   on `deviceUid` and `caregivers`. Until they are deployed, production
   mode refuses every write and the console's **Cloud backup** line says
   `PERMISSION_DENIED`. Never deploy open rules to the real project: any
   signed-in device could then read every family's data.
6. Project settings → General → **Your apps** → add a Web app. From its
   config copy `apiKey`, `projectId` and `storageBucket`.
7. On the tablet: gear → Developer console → **Keys & settings**: fill
   `FIREBASE_API_KEY`, `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`,
   Save. On a laptop, put the same three in `~/.saathi/env`. All three or
   none: with only some, the console's notes name the missing ones.
8. The console's Overview shows **Cloud backup**: uploaded / waiting /
   last error.

The web API key identifies the project; it grants nothing by itself.
What a signed-in device may read or write is the rules' job.

## Layout (for the caregiver app)

```
Firestore  devices/<device-id>
               {deviceUid, caregivers: []}
               made on the first upload, never overwritten
Firestore  devices/<device-id>/turns/<turn-id>
               {ts, heard, said, language, clipPath?}
Storage    devices/<device-id>/audio/<turn-id>.wav        16 kHz mono
Storage    devices/<device-id>/memory/<YYYYMMDDTHHMMSSZ>.sqlite3
```

- `<device-id>` is made once per device (`<data dir>/device-id`).
- `deviceUid` is the device's Firebase Auth uid; `caregivers` is filled
  by pairing.
- `<turn-id>` is `<UTC time>-<random>`, so turn ids sort in time order;
  `ts` is a Firestore timestamp.
- `said` is Saathi's reply. `language` is the language she was being
  spoken to in.
- `clipPath` is the Storage path of that turn's clip, when there is one.
- The memory file is a consistent sqlite copy with the schema in SPEC.md
  (entities, episodes, rules, preferences, reminders, ...). It goes up at
  most every 5 minutes, 20 s after a turn.

## Offline

Uploads wait in `<data dir>/cloud/outbox` and go up in order once the
network is back, across restarts, backing off up to 5 minutes between
tries. Each exchange is also appended to
`<data dir>/transcripts/<YYYY-MM-DD>.jsonl` on the device.

## Testing against the emulators

`tests/test_cloud_firebase_emulator.py` runs only when the emulators
are up (`FIRESTORE_EMULATOR_HOST` set). From a directory holding a
`firebase.json` with open test rules:

```
firebase emulators:exec --only auth,firestore,storage --project demo-saathi \
  "cd <repo> && env PYTHONPATH= python -m pytest -q -p no:cacheprovider tests/test_cloud_firebase_emulator.py"
```

The client picks the emulators up from the standard
`FIREBASE_AUTH_EMULATOR_HOST`, `FIRESTORE_EMULATOR_HOST` and
`FIREBASE_STORAGE_EMULATOR_HOST`.
