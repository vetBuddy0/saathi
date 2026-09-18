#!/usr/bin/env bash
# Takes a fresh 64-bit Raspberry Pi OS install (with this repo already
# checked out somewhere on it — this script does not clone anything) to
# a Pi that boots straight into Saathi's face on its monitor.
#
# Written and reviewed, never run: there is no Pi to test this against.
# Every step is designed to fail loudly rather than half-succeed, and the
# riskiest assumptions are called out inline with "UNVERIFIED". Read the
# report this shipped with for the full list before relying on it.
#
# Usage: sudo scripts/setup-pi.sh
#
# Idempotent: re-running is safe. Packages, the system user, and secrets
# are only touched if missing; systemd units are always rewritten to the
# current content and both services restarted, so re-running after
# 'git pull' picks up code changes without hand-holding.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAATHI_USER="saathi"
ENV_FILE="/etc/saathi/env"
SYSTEMD_DIR="/etc/systemd/system"
UV_BIN=""  # resolved once the saathi user has uv installed

log() { printf '[setup-pi] %s\n' "$*"; }
die() {
    printf '\n[setup-pi] FAILED: %s\n' "$*" >&2
    exit 1
}

# -- preflight ----------------------------------------------------------

preflight() {
    [ "$(id -u)" -eq 0 ] || die "must be run as root: sudo scripts/setup-pi.sh"
    command -v apt-get >/dev/null 2>&1 || die "apt-get not found — this script only supports Debian-based systems (Raspberry Pi OS)"

    local arch
    arch="$(uname -m)"
    if [ "$arch" != "aarch64" ]; then
        log "WARNING: uname -m is '$arch', not aarch64. This script was written for 64-bit Raspberry Pi OS and every wheel/binary assumption below is for that architecture. Continuing, but expect failures."
    fi
}

# A Pi has no RTC: every cold boot starts with a wrong date until NTP
# lands, and until it does, every HTTPS call below (apt, curl, uv, pip
# wheels, Piper voice downloads) fails on certificate validation —
# "certificate is not yet valid" — which reads like a bad network or a
# bad key and is neither. Runs first, before any of those.
wait_for_clock_sync() {
    if ! command -v timedatectl >/dev/null 2>&1; then
        log "WARNING: timedatectl not found — can't confirm the clock is synced. If network steps below fail with a certificate-date error, that's why."
        return
    fi

    if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]; then
        log "Clock already synchronized"
        return
    fi

    log "Waiting for the clock to sync via NTP before touching the network..."
    systemctl enable --now systemd-timesyncd.service 2>/dev/null || true

    local tries=0
    until [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] || [ "$tries" -ge 60 ]; do
        sleep 1
        tries=$((tries + 1))
    done

    if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]; then
        log "Clock synchronized ($(date))"
    else
        die "clock did not sync via NTP after 60s. Check network access and 'timedatectl status' by hand. Every network step in this script (apt, curl, uv, Piper voice downloads) will fail with confusing certificate-date errors until the clock is right — that is the actual cause if they do, not a bad key or a bad mirror."
    fi
}

# Re-running this script is meant to be safe (it's how you pick up a
# `git pull`), but installing *on top of a different, older build* is
# not the same thing — a leftover display manager, a stale autologin
# override for some other user, or a process that already has the mic
# open would all make the real checks below (saathi smoke, the face
# actually appearing) fail for a reason that has nothing to do with
# this script. Checked once, early, before anything is installed.
check_for_conflicts() {
    log "Checking for anything that would conflict with this install"
    local problems=()

    local other_display_unit
    for other_display_unit in lightdm.service sddm.service gdm.service gdm3.service; do
        if systemctl is-enabled "$other_display_unit" >/dev/null 2>&1; then
            problems+=(
                "$other_display_unit is enabled -- it will fight saathi-face.service for the display"
            )
        fi
    done

    local autologin_conf="/etc/systemd/system/getty@tty1.service.d/autologin.conf"
    if [ -f "$autologin_conf" ] && ! grep -q "$SAATHI_USER" "$autologin_conf" 2>/dev/null; then
        problems+=(
            "$autologin_conf exists and doesn't mention '$SAATHI_USER' -- a previous, non-Saathi kiosk setup may already own tty1"
        )
    fi

    if command -v fuser >/dev/null 2>&1 && [ -d /dev/snd ]; then
        local holders
        holders="$(fuser /dev/snd/* 2>/dev/null || true)"
        if [ -n "$holders" ]; then
            problems+=(
                "something already has /dev/snd open ($holders) -- likely a leftover process from a previous install, holding the mic or speaker"
            )
        fi
    fi

    # A saathi-engine/-face unit already existing isn't itself a
    # conflict -- re-running this script to update one is the point.
    # Only flag it if it's running as some other user, which this script
    # never configures -- that means a differently-built previous
    # install, not a stale run of this one.
    local unit existing_user
    for unit in saathi-engine.service saathi-face.service; do
        existing_user="$(systemctl show "$unit" --property=User --value 2>/dev/null || true)"
        if [ -n "$existing_user" ] && [ "$existing_user" != "$SAATHI_USER" ]; then
            problems+=(
                "$unit already exists and runs as '$existing_user', not '$SAATHI_USER' -- looks like a previous, differently-configured install"
            )
        fi
    done

    if [ "${#problems[@]}" -gt 0 ]; then
        log "Found ${#problems[@]} potential conflict(s) with an existing setup:"
        local problem
        for problem in "${problems[@]}"; do
            log "  - $problem"
        done
        die "resolve the above before continuing. Installing on top of a conflicting setup is how a kiosk ends up pointed at hardware something else already owns. Nothing below this point has been touched -- re-run once they're cleared."
    fi
    log "No conflicts found"
}

# -- system packages ------------------------------------------------------

APT_UPDATED=0
apt_update_once() {
    if [ "$APT_UPDATED" -eq 0 ]; then
        log "apt-get update"
        apt-get update -qq || die "apt-get update failed — check network access, then re-run"
        APT_UPDATED=1
    fi
}

apt_install() {
    apt_update_once
    log "Installing: $*"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" \
        || die "apt-get install failed for: $* — see the apt output above for which package and why, fix that, then re-run"
}

# UNVERIFIED: 'cage' and 'seatd' are standard Debian package names and
# should be in Raspberry Pi OS's repos (which mirror Debian's), but this
# was never confirmed against a real Pi OS apt cache.
install_base_packages() {
    apt_install pulseaudio pulseaudio-utils cage seatd curl ca-certificates unzip
}

# UNVERIFIED: Raspberry Pi OS Bookworm is expected to package this as
# 'chromium' (matching Debian), with 'chromium-browser' as the name used
# by some older Raspberry Pi OS releases. Tries both; the kiosk launcher
# script (scripts/saathi-face-kiosk.sh) also checks both names at
# runtime independently, so a mismatch here doesn't silently break it —
# it just means this step didn't need to do anything.
install_chromium() {
    if command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then
        log "Chromium already installed"
        return
    fi
    apt_update_once
    if apt-cache show chromium >/dev/null 2>&1; then
        apt_install chromium
    elif apt-cache show chromium-browser >/dev/null 2>&1; then
        apt_install chromium-browser
    else
        die "neither 'chromium' nor 'chromium-browser' is available via apt-cache. Run 'apt-cache search chromium' by hand, install whichever package it finds, then re-run this script."
    fi
}

enable_seatd() {
    # Fallback device-access path for cage (wlroots/libseat), alongside
    # the PAMName=login session the face service also gets. UNVERIFIED
    # whether this is actually needed on top of PAMName=login, or
    # redundant — see the report. Warning, not fatal: if cage can't get
    # the display either way, 'journalctl -u saathi-face' will say so.
    systemctl enable --now seatd 2>/dev/null \
        || log "WARNING: could not enable seatd. If saathi-face.service fails to acquire the display, check 'journalctl -u saathi-face' and revisit this."
}

# -- the saathi system user ------------------------------------------------

ensure_saathi_user() {
    if id "$SAATHI_USER" >/dev/null 2>&1; then
        log "System user '$SAATHI_USER' already exists"
    else
        log "Creating system user '$SAATHI_USER'"
        useradd --system --create-home --home-dir "/home/$SAATHI_USER" --shell /bin/bash "$SAATHI_USER" \
            || die "useradd failed for '$SAATHI_USER'"
    fi

    # Only groups that actually exist on this system — 'render' in
    # particular is created by udev for GPU nodes and may not exist on
    # every image, and usermod fails outright on an unknown group.
    local wanted=(audio video input tty render) present=()
    for g in "${wanted[@]}"; do
        getent group "$g" >/dev/null 2>&1 && present+=("$g")
    done
    if [ "${#present[@]}" -gt 0 ]; then
        local joined
        joined="$(IFS=,; echo "${present[*]}")"
        usermod --append --groups "$joined" "$SAATHI_USER" \
            || die "usermod failed adding '$SAATHI_USER' to groups: $joined"
    fi

    log "Handing ownership of $REPO_ROOT to $SAATHI_USER"
    chown -R "$SAATHI_USER:$SAATHI_USER" "$REPO_ROOT" \
        || die "chown failed on $REPO_ROOT — the saathi user needs to own its working copy to run uv and write its venv"
}

# -- uv + python deps -------------------------------------------------------

run_as_saathi() {
    sudo -u "$SAATHI_USER" bash -c "$1"
}

install_uv() {
    local uv_path="/home/$SAATHI_USER/.local/bin/uv"
    if [ -x "$uv_path" ]; then
        log "uv already installed for $SAATHI_USER"
    else
        log "Installing uv for $SAATHI_USER"
        # UNVERIFIED-adjacent: astral's installer has, in the past,
        # changed its default install directory. If this fails to find
        # uv afterwards, that's the first thing to check.
        run_as_saathi 'curl -LsSf https://astral.sh/uv/install.sh | sh' \
            || die "uv install script failed for $SAATHI_USER"
    fi
    [ -x "$uv_path" ] || die "uv install appeared to succeed but $uv_path doesn't exist — check the installer output above; it may have used a different path this time"
    UV_BIN="$uv_path"
}

install_python_deps() {
    log "Resolving the pinned Python interpreter and project dependencies (uv sync)"
    # UNVERIFIED: this downloads a prebuilt CPython for aarch64 via uv's
    # own python-build-standalone distributions. Believed to exist for
    # aarch64 Linux, never confirmed against a real Pi.
    run_as_saathi "cd '$REPO_ROOT' && '$UV_BIN' sync" \
        || die "uv sync failed. If it failed fetching a Python build for aarch64, that's the UNVERIFIED path flagged above — check the error, and 'uv python list' to see what's actually available."
}

# -- Piper voices -----------------------------------------------------------

install_piper_voices() {
    log "Downloading Piper voices for every supported language"
    # Sourced from the running code, not hand-copied — one source of
    # truth (saathi/voice/language.py) for which languages are real.
    local voices
    voices="$(run_as_saathi "cd '$REPO_ROOT' && '$UV_BIN' run python3 -c \"from saathi.voice.language import SUPPORTED_LANGUAGES; print(' '.join(SUPPORTED_LANGUAGES.values()))\"")" \
        || die "could not read the supported-voice list from saathi.voice.language — has that module moved?"

    local voice_dir="/home/$SAATHI_USER/.saathi/tts-voices"
    local voice
    for voice in $voices; do
        if [ -f "$voice_dir/$voice.onnx" ]; then
            log "  $voice: already downloaded"
            continue
        fi
        log "  $voice: downloading"
        # Confirmed on real Pi hardware: the aarch64 wheel concern this
        # comment used to flag as unverified was not the problem. This
        # line was, though — it ran without cd'ing into $REPO_ROOT first,
        # so it inherited the caller's cwd. On Pi OS Bookworm+, a mode
        # 0700 home directory the saathi user can't read makes uv fail
        # outright trying to look for a uv.toml there:
        #   error: failed to open file `/home/<caller>/uv.toml`: Permission denied
        # cd into a directory saathi actually owns first, same as
        # install_python_deps above.
        run_as_saathi "cd '$REPO_ROOT' && '$UV_BIN' run python3 -m piper.download_voices '$voice' --download-dir '$voice_dir'" \
            || die "failed to download Piper voice '$voice'. Check network access and the output above."
    done
}

# -- secrets ------------------------------------------------------------

# Leak audit (checked, not assumed, after a real install): `read -s`
# suppresses terminal echo of what's typed/pasted at the TTY level, below
# where a `| tee` on this script's own stdout/stderr could ever see it —
# there is nothing for tee to capture. Neither `prompt_secret` nor
# anything it calls ever passes `$value` to `log`, `die`, or any other
# print path; the one validation message below that could have been
# tempted to show the pasted value shows its *length* instead. No `set
# -x` anywhere in this file, which is the other way a variable's value
# can end up on stderr uninvited (xtrace prints expanded commands).
#
# A real paste on a real install once produced a 174-character value
# from a 56-character key (see below) — root cause not reproducible
# without that exact terminal/remote-console setup, so this validates
# defensively instead of trying to fix the paste path itself.
prompt_secret() {
    local var_name="$1" prompt_text="$2" validate_regex="${3:-}"
    mkdir -p "$(dirname "$ENV_FILE")"
    touch "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    chown root:root "$ENV_FILE"

    if grep -q "^${var_name}=" "$ENV_FILE"; then
        if [ -t 0 ]; then
            local keep
            read -r -p "${var_name} is already set in ${ENV_FILE}. Keep it? [Y/n] " keep
            case "$keep" in
                [nN]*) sed -i "/^${var_name}=/d" "$ENV_FILE" ;;
                *) log "Keeping existing ${var_name}"; return 0 ;;
            esac
        else
            log "${var_name} already set in ${ENV_FILE}; not interactive, keeping it"
            return 0
        fi
    elif [ ! -t 0 ]; then
        die "${var_name} is not set in ${ENV_FILE} and this shell isn't interactive, so it can't be prompted for. Run this script from an interactive terminal at least once, or write ${ENV_FILE} by hand (mode 0600, root-owned) first."
    fi

    local value=""
    while :; do
        read -r -s -p "${prompt_text}: " value
        echo
        # A mangled paste has shown up as stray whitespace/control
        # characters (embedded carriage returns in particular — bash's
        # `read` only terminates on a bare newline, so a paste using \r
        # as its line ending reads as one unbroken, wrong-length string)
        # rather than the clean value that was actually copied. Strip
        # what a normal single paste would never contain.
        value="$(printf '%s' "$value" | tr -d '[:space:]')"
        if [ -z "$value" ]; then
            echo "  (empty — try again)"
            continue
        fi
        if [ -n "$validate_regex" ] && ! [[ "$value" =~ $validate_regex ]]; then
            echo "  That doesn't look right (got ${#value} characters after trimming whitespace)."
            echo "  If you pasted it, check the paste landed once, not doubled or tripled."
            continue
        fi
        break
    done
    printf '%s=%s\n' "$var_name" "$value" >> "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    unset value
    log "${var_name} written to ${ENV_FILE} (mode 0600, root-owned)"
}

# -- hardware check -----------------------------------------------------

SAATHI_UID=""
SAATHI_RUNTIME_DIR=""

enable_linger_and_wait_for_pulseaudio() {
    log "Enabling a lingering session for $SAATHI_USER (PulseAudio needs a real user session, even with no one logged in)"
    loginctl enable-linger "$SAATHI_USER" || die "loginctl enable-linger failed for $SAATHI_USER"

    SAATHI_UID="$(id -u "$SAATHI_USER")"
    SAATHI_RUNTIME_DIR="/run/user/$SAATHI_UID"

    # UNVERIFIED: the exact timing of a freshly-enabled lingering
    # session's PulseAudio auto-spawn. Waits up to 15s for its socket,
    # then moves on regardless — the smoke check right after this is the
    # thing that actually proves (or disproves) it worked.
    local tries=0
    until [ -S "$SAATHI_RUNTIME_DIR/pulse/native" ] || [ "$tries" -ge 15 ]; do
        sleep 1
        tries=$((tries + 1))
    done
    if [ ! -S "$SAATHI_RUNTIME_DIR/pulse/native" ]; then
        log "WARNING: no PulseAudio socket at $SAATHI_RUNTIME_DIR/pulse/native after 15s. Continuing — the hardware check next will fail clearly if this is actually broken."
    fi
}

run_smoke_check() {
    log "Running 'saathi smoke' as $SAATHI_USER — stopping here if hardware is missing"
    if ! sudo -u "$SAATHI_USER" env XDG_RUNTIME_DIR="$SAATHI_RUNTIME_DIR" \
        bash -c "cd '$REPO_ROOT' && '$UV_BIN' run saathi smoke"; then
        die "saathi smoke reports missing or unusable audio hardware (see output above). Not installing a kiosk pointed at a device that can't hear or speak. Fix the microphone/speaker, then re-run this script — everything before this point is already done and won't be redone."
    fi
    log "Hardware check passed"
}

# -- kiosk display: console blanking, tty1, systemd units -----------------

disable_console_blanking() {
    local cmdline_file=""
    for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
        [ -f "$candidate" ] && cmdline_file="$candidate" && break
    done
    if [ -z "$cmdline_file" ]; then
        log "WARNING: no cmdline.txt found in /boot/firmware or /boot — skipping consoleblank=0. Add it to the kernel command line by hand if the screen blanks."
        return
    fi
    if grep -q 'consoleblank=0' "$cmdline_file"; then
        log "Console blanking already disabled in $cmdline_file"
        return
    fi
    log "Disabling console blanking in $cmdline_file (takes effect after reboot)"
    cp "$cmdline_file" "$cmdline_file.saathi-backup"
    sed -i 's/$/ consoleblank=0/' "$cmdline_file" || die "failed to edit $cmdline_file"
}

# tty1 is the face's, not a login prompt's. Masking getty@tty1 rather
# than configuring agetty autologin: saathi-face.service (below) owns
# tty1 directly via PAMName=login + TTYPath, so nothing should be racing
# it for that console. tty2 onward keep their default gettys untouched —
# that's the escape hatch, and it needs no configuration at all.
claim_tty1_for_the_face() {
    log "Masking getty@tty1.service (the face owns tty1 instead)"
    systemctl mask getty@tty1.service || die "failed to mask getty@tty1.service"
}

write_systemd_units() {
    local cage_bin
    cage_bin="$(command -v cage)" || die "cage not found on PATH after installation — this shouldn't happen"

    log "Writing saathi-engine.service"
    # After=time-sync.target, not just network-online.target: a Pi has
    # no RTC, so it boots with a wrong date until NTP lands, and every
    # HTTPS call the engine makes to Groq fails with a certificate-date
    # error until then -- which reads like a bad key or no network and
    # is neither. network-online.target only promises routability, not
    # a correct clock.
    cat > "$SYSTEMD_DIR/saathi-engine.service" <<EOF
[Unit]
Description=Saathi core state machine and screen server
After=network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
Type=simple
User=$SAATHI_USER
WorkingDirectory=$REPO_ROOT
Environment=XDG_RUNTIME_DIR=/run/user/$SAATHI_UID
# Read once, at process start, not on every access. Editing this file
# (e.g. rotating GROQ_API_KEY) needs 'systemctl restart saathi-engine'
# to actually take effect -- it will keep running with the old
# environment otherwise, silently.
EnvironmentFile=$ENV_FILE
ExecStart=$UV_BIN run saathi run
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

    log "Writing saathi-face.service"
    # UNVERIFIED: PAMName=login + TTYPath is the standard systemd pattern
    # for "a service that owns a VT the way a getty would", and should
    # give cage a real logind session for DRM/input device access without
    # needing to run as root. Never confirmed against real Pi graphics
    # hardware (the vc4/KMS display stack) or a real cage binary.
    #
    # Confirmed wrong on a real Pi: WantedBy=graphical.target used to be
    # here on the assumption a kiosk service belongs with a graphical
    # session. Pi OS Lite boots to multi-user.target by default, so
    # graphical.target is never reached and the face never starts after
    # a real reboot -- caught only because someone ran
    # 'systemctl set-default graphical.target' by hand to work around it.
    # WantedBy=multi-user.target below is what actually gets pulled in on
    # this image; it doesn't need a graphical session; it *is* one.
    cat > "$SYSTEMD_DIR/saathi-face.service" <<EOF
[Unit]
Description=Saathi face (cage + Chromium kiosk)
After=saathi-engine.service
Wants=saathi-engine.service
Conflicts=getty@tty1.service

[Service]
Type=simple
User=$SAATHI_USER
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty
StandardOutput=journal
StandardError=journal
ExecStart=$cage_bin -- $REPO_ROOT/scripts/saathi-face-kiosk.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

    chmod +x "$REPO_ROOT/scripts/saathi-face-kiosk.sh" \
        || die "failed to make scripts/saathi-face-kiosk.sh executable"

    log "systemctl daemon-reload"
    systemctl daemon-reload || die "systemctl daemon-reload failed"

    log "Enabling and (re)starting both services"
    systemctl enable saathi-engine.service saathi-face.service \
        || die "systemctl enable failed — check the unit files in $SYSTEMD_DIR"
    systemctl restart saathi-engine.service \
        || die "saathi-engine.service failed to start — check 'journalctl -u saathi-engine -e'"
    systemctl restart saathi-face.service \
        || die "saathi-face.service failed to start — check 'journalctl -u saathi-face -e'. This is the most likely place for the cage/tty wiring to turn out wrong; see the report this script shipped with."
}

print_summary() {
    cat <<EOF

======================================================================
Saathi is set up.

Escape hatch:
  Ctrl+Alt+F2   switch to a normal terminal login (tty2)
  Ctrl+Alt+F1   switch back to the face (tty1)

Services:
  sudo systemctl stop  saathi-engine saathi-face
  sudo systemctl start saathi-engine saathi-face
  sudo systemctl status saathi-engine saathi-face

Logs:
  journalctl -u saathi-engine -f
  journalctl -u saathi-face -f

Editing $ENV_FILE by hand (e.g. to rotate GROQ_API_KEY) takes
effect only on the engine's next start — systemd reads an
EnvironmentFile once, at process start, not on every access. Run:
  sudo systemctl restart saathi-engine
after any edit, or the engine keeps using whatever was in the
environment when it last started.

Re-running this script is safe. It will not duplicate packages, the
system user, or the secret in $ENV_FILE, and it will rewrite the
systemd units and restart both services — so re-running after a
'git pull' picks up code changes.
======================================================================
EOF
}

main() {
    preflight
    wait_for_clock_sync
    check_for_conflicts
    install_base_packages
    enable_seatd
    install_chromium
    ensure_saathi_user
    install_uv
    install_python_deps
    enable_linger_and_wait_for_pulseaudio
    run_smoke_check  # fail fast on hardware, before voices/secrets/kiosk setup
    install_piper_voices
    # Confirmed against a real key: gsk_ + 52 chars = 56 total. Some
    # slack either side (44-64) in case key length varies by account
    # tier or a future rotation — this is a sanity check against a
    # mangled paste, not a checksum, so it doesn't need to be exact.
    prompt_secret GROQ_API_KEY "Groq API key (console.groq.com)" '^gsk_[A-Za-z0-9]{40,60}$'
    disable_console_blanking
    claim_tty1_for_the_face
    write_systemd_units
    print_summary
}

main "$@"
