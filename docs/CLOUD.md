# Cloud copy (AWS S3)

Every exchange, the clip the transcriber heard, and the memory file are
copied to one S3 bucket (`saathi/cloud/sync.py`). The device stays the
source of truth; the bucket is what a caregiver or family portal reads.

## Setting it up (once)

1. AWS console → S3 → **Create bucket**. Name it (e.g. `saathi-data-<yours>`),
   pick the region nearest the device (e.g. `ap-southeast-1`, Singapore).
   Keep **Block all public access** on. Optionally turn on default
   encryption (SSE-S3, free) and versioning.
2. IAM → Users → **Create user** `saathi-device`, no console access.
   Attach this inline policy (replace the bucket name):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "s3:PutObject",
       "Resource": "arn:aws:s3:::saathi-data-<yours>/devices/*"
     }]
   }
   ```

   The device can only add files under `devices/`; it cannot read, list
   or delete anything, so a stolen tablet key can't read other data.
3. That user → Security credentials → **Create access key** (use case:
   "Application running outside AWS"). Copy the key id and secret.
4. On the tablet: gear → Developer console → **Keys & settings**: fill
   `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
   `AWS_S3_BUCKET`, Save. On a laptop, put the same four in `~/.saathi/env`.
5. The console's Overview shows **Cloud backup**: uploaded / waiting /
   last error.

## Layout (for the portal)

```
devices/<device-id>/transcripts/<YYYY-MM-DD>/<time>-<turn>.json
    {"device_id", "turn_id", "ts", "heard", "reply", "audio_key"}
devices/<device-id>/audio/<YYYY-MM-DD>/<time>-<turn>.wav     16 kHz mono
devices/<device-id>/memory/identity-latest.sqlite3
devices/<device-id>/memory/daily/identity-<YYYY-MM-DD>.sqlite3
```

`<device-id>` is made once per device (`<data dir>/device-id`); the
portal maps it to a person. A day's conversation is one prefix listing.
The sqlite file has the schema in SPEC.md (entities, episodes, rules,
preferences, reminders, ...).
