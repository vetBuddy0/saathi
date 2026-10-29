# functions/: caregiver push notifications

Caregivers need to know on their phones, even with the app closed, when
she misses a reminder or answers them. The tablet can't send that push
because it holds no service account (on purpose), so these Cloud
Functions send it. They react to documents the device already writes.

> **Not deployed.** Cloud Functions need the Firebase **Blaze**
> (pay-as-you-go) plan. Blaze means attaching a billing account, which
> is the owner's call and has **not** been confirmed. Everything here
> runs and is tested in the local emulator only.

## What each function does

| Function | Fires on | Who is told | Body |
|---|---|---|---|
| `onEventCreated` | `devices/{id}/events/{eventId}` created with `kind == "missed"` | every FCM token of every uid in `devices/{id}.caregivers` | `{device name} didn't confirm: {text}` |
| `onReplyCreated` | `devices/{id}/inbox/{msgId}/replies/{replyId}` created | the message's `fromUid` only | `{device name} replied: {text}` |
| `onMessageSpoken` | `devices/{id}/inbox/{msgId}` updated, and `spokenAt` was just set for the first time | the message's `fromUid` only | `{device name} heard your message` |

Every notification has the title `Saathi`. Its `data` carries `type`,
`deviceId` and the ids (`eventId`, or `msgId`/`replyId`), so the
caregiver app can open the right screen.

Rules that are easy to miss:
- Only `missed` events notify anyone. `fired`, `acked`, `timer_done` and
  `message_spoken` are dropped before any read.
- Replies and "heard" notices go to the sender only while the sender is
  still in `caregivers`. A caregiver who was removed stops hearing her
  replies.
- If one token is shared by two caregivers, it gets one push.
- Text longer than 200 characters is clipped. An unnamed device is
  called "Saathi".
- **Housekeeping:** if FCM says a token is dead
  (`messaging/registration-token-not-registered` or
  `messaging/invalid-registration-token`), that token is removed from
  `users/{uid}.fcmTokens`. Other errors, like quota or a transient
  failure, never remove a token.
- **Retries are on.** A failed read or FCM call throws, and the trigger
  is retried. After 15 minutes it gives up, because a "didn't take her
  medicine" push a day late is noise. A failed token prune is logged,
  not retried, so pushes that already went out aren't sent again. There
  is no idempotency key, so a rare redelivery can send a duplicate push.
  For a missed medicine, a duplicate is better than a lost alert.
- `messaging/invalid-argument` never prunes. FCM also returns it for a
  bad payload, and a payload bug would otherwise wipe every token.
- Every notifiable trigger logs, including "nothing delivered (no
  tokens)", so a missing alert can be traced.

## Layout

- `src/notify.ts`: pure logic: `recipientUids`, `notificationBody`,
  `buildNotifications`, `spokenNow`.
- `src/deliver.ts`: read, send, prune. Takes a `Store` and a `Sender`,
  so it is tested with fakes.
- `src/senders.ts`: the FCM sender (`sendEach`, in batches of 500) and
  the emulator-only outbox sender.
- `src/store.ts`: the `Store`, built on the Admin SDK.
- `src/index.ts`: thin trigger wrappers, region `asia-south1`,
  `maxInstances: 5`.

## Test

Use the preinstalled Node 22 and JDK 21 (the emulator needs Java):

```sh
export PATH=$HOME/opt/node/bin:$HOME/opt/jdk21/bin:$PATH JAVA_HOME=$HOME/opt/jdk21
cd functions
npm ci
npm run build      # tsc
npm test           # unit tests (node:test), fake Store/Sender/messaging
npm run e2e        # build, then emulators:exec running test/e2e
```

`npm run e2e` runs:

```sh
firebase emulators:exec --config firebase.test.json --only functions,firestore \
  --project demo-saathi "npm run test:e2e"
```

The emulator can't reach FCM. Under `demo-saathi`, `.env.demo-saathi`
sets `SAATHI_FCM_OUTBOX=1`. Each intended send is then written to
`_test_outbox`, and the test reads that collection. Tokens starting with
`test-unregistered-` come back as dead, which exercises pruning. Outbox
mode also needs `FUNCTIONS_EMULATOR=true`, so it can't be switched on in
production. `firestore.test.rules` is open and used only by the
emulator. The real rules live in `contracts/`.

## Cost

At this scale, expect about **$0**. Firestore triggers can only filter
by path, so every event write runs `onEventCreated`, and every inbox
edit runs `onMessageSpoken`. A device writes tens of events a day,
which is a few thousand invocations a month. Blaze includes 2M
invocations a month free, and FCM is free. Container images for the
functions use a little Artifact Registry storage, a few cents a month
at most.

It's still a billing account with no hard cap, so it's the owner's
decision. If they go ahead, set a budget alert (for example $5) in
Cloud Billing at the same time.

## Deploying (only after the owner confirms Blaze)

1. Upgrade the project to Blaze in the Firebase console.
2. Check that `REGION` in `src/index.ts` matches the Firestore database
   location. Firestore triggers can't cross regions. The code assumes
   `asia-south1`.
3. Point `firebase.json` at this directory. If the repo's
   `firebase.json` doesn't declare it yet, add
   `"functions": [{ "source": "functions", "codebase": "default" }]`.
4. From the directory holding that `firebase.json`:

```sh
npm --prefix functions ci && npm --prefix functions run build
firebase deploy --only functions --project <project-id>
```

`firebase.test.json` is for the emulator only. Never deploy with it.

## Known constraints

- `firebase-admin` is pinned to 13.x. Version 14 depends on
  `jwks-rsa@4`, which `require()`s an ES module. That needs Node 22.12
  or later, and this machine has 22.11, so the emulator can't load it.
  The deployed runtime would be fine. Move to 14 once the host Node is
  22.12 or later. `npm audit` reports moderate `uuid` advisories through
  `@google-cloud/firestore`. They're in a buffer-argument path this code
  never calls, and firebase-admin 14 fixes them.
