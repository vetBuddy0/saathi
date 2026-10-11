/**
 * The component `dpm set-device-owner` points at.
 *
 * Why this file exists: lock-task mode without a "pin this app?" prompt,
 * and a HOME that cannot be changed, both need the app to be Device
 * Owner, and Device Owner is granted to a DeviceAdminReceiver by name
 * (`com.saathi.shell/.SaathiAdminReceiver`, see README.md). It has no
 * behaviour of its own: every callback below logs and defers to the
 * platform, because the policies that matter are set by `Kiosk` when
 * the activity asks for them, not from a broadcast. The callbacks are
 * overridden at all so that logcat says when the owner was granted,
 * when a lock task began or ended, and if anything ever asked to remove
 * the admin -- the three questions asked first when a tablet is found
 * showing a launcher instead of the face.
 *
 * What lost: a managed-provisioning flow (NFC bump or QR at setup).
 * Correct, and the way a fleet would do it, but it needs a hosted APK
 * and a signing checksum, and for one tablet in one living room an
 * `adb` line after a factory reset is the whole procedure. Also lost:
 * applying the kiosk policies from `onEnabled` or
 * `onProfileProvisioningComplete`. Neither fires for `dpm
 * set-device-owner` on every release, and a policy that depends on
 * which path granted the owner is one that is missing on the day it
 * matters.
 */
package com.saathi.shell

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class SaathiAdminReceiver : DeviceAdminReceiver() {
    override fun onEnabled(context: Context, intent: Intent) {
        super.onEnabled(context, intent)
        Log.i(TAG, "device admin enabled")
    }

    override fun onDisableRequested(context: Context, intent: Intent): CharSequence? {
        Log.w(TAG, "device admin disable requested")
        return super.onDisableRequested(context, intent)
    }

    override fun onDisabled(context: Context, intent: Intent) {
        super.onDisabled(context, intent)
        Log.w(TAG, "device admin disabled")
    }

    override fun onLockTaskModeEntering(context: Context, intent: Intent, pkg: String) {
        super.onLockTaskModeEntering(context, intent, pkg)
        Log.i(TAG, "lock task entered by $pkg")
    }

    override fun onLockTaskModeExiting(context: Context, intent: Intent) {
        super.onLockTaskModeExiting(context, intent)
        Log.i(TAG, "lock task exited")
    }

    override fun onProfileProvisioningComplete(context: Context, intent: Intent) {
        super.onProfileProvisioningComplete(context, intent)
        Log.i(TAG, "provisioning complete")
    }

    private companion object {
        const val TAG = "SaathiKiosk"
    }
}
