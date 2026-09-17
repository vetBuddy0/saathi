#!/usr/bin/env bash
# Launched by cage (itself launched by saathi-face.service) as the single
# kiosk application. Not meant to be run by hand except for debugging —
# it execs, it doesn't return, and it assumes cage is already the Wayland
# compositor in charge of the display it's drawing to.
#
# UNVERIFIED (no Pi to test on — see scripts/setup-pi.sh's header and the
# report this shipped with): the exact Chromium flags for running cleanly
# under cage's Wayland compositor, and whether the cursor stays hidden
# without an X11-only tool like unclutter (which cannot work here — cage
# is Wayland, not X). If the cursor is visible and that matters, that is
# the first thing to revisit.
set -euo pipefail

PORT="${SAATHI_SCREEN_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"

# Retry until the engine actually answers, so the first thing on screen
# is never a browser's own connection-error page — SPEC.md's "no status
# text" applies to more than just the face's own UI.
echo "saathi-face-kiosk: waiting for ${URL} ..."
until curl --silent --fail --max-time 2 --output /dev/null "$URL"; do
    sleep 1
done
echo "saathi-face-kiosk: engine is up, starting Chromium"

CHROMIUM_BIN=""
for candidate in chromium chromium-browser; do
    if command -v "$candidate" >/dev/null 2>&1; then
        CHROMIUM_BIN="$candidate"
        break
    fi
done
if [ -z "$CHROMIUM_BIN" ]; then
    echo "saathi-face-kiosk: no chromium binary found (tried: chromium, chromium-browser)" >&2
    exit 1
fi

exec "$CHROMIUM_BIN" \
    --kiosk \
    --ozone-platform=wayland \
    --enable-features=UseOzonePlatform \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --disable-session-crashed-bubble \
    --disable-translate \
    --overscroll-history-navigation=0 \
    --disable-pinch \
    --autoplay-policy=no-user-gesture-required \
    --check-for-update-interval=31536000 \
    --password-store=basic \
    "$URL"
