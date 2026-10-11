/**
 * The tablet that shows only Saathi: lock-task mode and the Device Owner
 * policies that make it hold.
 *
 * Why this file exists: a kiosk is not one call. `startLockTask()` on its
 * own shows a "pin this screen?" prompt, leaves the status bar to pull
 * down, lets the keyguard come up after a dim, and loses the launcher to
 * the next "which home app?" chooser -- each a way for the device to
 * stop being the face by the time anyone checks on it. Device Owner is
 * what removes those one by one, and every one of its calls throws
 * `SecurityException` in a build that is not owner (the demo phone,
 * every debug install). So the calls are here, each wrapped to log and
 * go on, and the activity asks for "kiosk" once rather than knowing
 * which seven policies that is. The pure parts (which packages, which
 * features) are plain values so they can be read by a test; a kiosk
 * that silently stops pinning after a refactor is the kind of bug that
 * shows up a month later in someone's living room.
 *
 * What lost: `lockTaskMode="always"` in the manifest with no code (it
 * pins only the owner's own task and still leaves the status bar and the
 * keyguard); a third-party kiosk launcher (one more app to keep signed
 * in, and a second place the engine address lives); and leaving the
 * policies in place on exit. [exitLockTask] undoes what [enterLockTaskIfOwner]
 * did to the screen -- the whitelist, the status bar, the keyguard --
 * because `lockTaskMode="if_whitelisted"` re-pins a whitelisted task on
 * its next launch and an "exit" that lasted until the next reboot is not
 * one. It leaves the persistent HOME and `DISALLOW_SAFE_BOOT` alone:
 * those make the shell the launcher, which the owner tablet stays
 * whether or not it is pinned, and a safe boot is a way around the owner
 * that nothing in the dialog should hand out.
 *
 * YouTube's own app is in the lock-task whitelist because the watch page
 * can hand a video to it (an "open in app" banner, a share sheet); a
 * package that is not whitelisted cannot start while the task is locked,
 * and the tap would simply do nothing.
 */
package com.saathi.shell

import android.app.Activity
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.UserManager
import android.util.Log

object Kiosk {
    private const val TAG = "SaathiKiosk"

    /** The one app besides this one that may come up while the screen is locked to the task. */
    const val YOUTUBE_PACKAGE = "com.google.android.youtube"

    /**
     * `DevicePolicyManager.LOCK_TASK_FEATURE_NONE`: no keyguard, no
     * notifications, no home or overview button, no global actions, no
     * system info in the bar. The screen is the face. Written as its
     * value so a test can compare it with the SDK's constant; API 28+
     * only, and before that lock task has no feature flags and behaves
     * the same.
     */
    const val LOCK_TASK_FEATURES = 0

    /** What [DevicePolicyManager.setLockTaskPackages] is given: this app first, then YouTube's. */
    fun lockTaskPackages(ownPackage: String): Array<String> = arrayOf(ownPackage, YOUTUBE_PACKAGE)

    /** True on the tablet after `dpm set-device-owner`; false on every phone and every debug install. */
    fun isDeviceOwner(context: Context): Boolean =
        guarded("isDeviceOwnerApp", false) {
            policy(context)?.isDeviceOwnerApp(context.packageName) == true
        }

    /** Whether this task is currently locked or pinned to the screen. */
    fun isLocked(context: Context): Boolean =
        guarded("lockTaskModeState", false) {
            val manager = context.getSystemService(ActivityManager::class.java)
            manager != null && manager.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE
        }

    /**
     * As Device Owner: whitelist this app and YouTube's for lock task,
     * turn every lock-task feature off, make this activity the HOME that
     * cannot be changed, disable the keyguard and the status bar, forbid
     * safe boot, then lock the task. Not owner: one log line and nothing
     * else -- a screen-pinning prompt is not a kiosk, and it is not hers
     * to answer.
     */
    fun enterLockTaskIfOwner(activity: Activity) {
        if (!isDeviceOwner(activity)) {
            Log.i(TAG, "not Device Owner; lock task not started")
            return
        }
        val policy = policy(activity) ?: return
        val admin = adminOf(activity)
        val home = ComponentName(activity, MainActivity::class.java)
        guarded("setLockTaskPackages") {
            policy.setLockTaskPackages(admin, lockTaskPackages(activity.packageName))
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            guarded("setLockTaskFeatures") { policy.setLockTaskFeatures(admin, LOCK_TASK_FEATURES) }
        }
        guarded("addPersistentPreferredActivity") {
            policy.addPersistentPreferredActivity(admin, homeFilter(), home)
        }
        guarded("setKeyguardDisabled") {
            // False when a screen lock is set: the owner cannot hide a
            // keyguard that protects a credential. Logged, not fatal.
            if (!policy.setKeyguardDisabled(admin, true)) Log.w(TAG, "keyguard stays: a screen lock is set")
        }
        guarded("setStatusBarDisabled") {
            if (!policy.setStatusBarDisabled(admin, true)) Log.w(TAG, "status bar could not be disabled")
        }
        guarded("addUserRestriction") { policy.addUserRestriction(admin, UserManager.DISALLOW_SAFE_BOOT) }
        if (isLocked(activity)) {
            Log.i(TAG, "already locked to the task")
            return
        }
        guarded("startLockTask") { activity.startLockTask() }
    }

    /**
     * Leave lock task (or screen pinning) and, as Device Owner, take back
     * the whitelist, the status bar and the keyguard so the next launch
     * does not pin itself again. Safe to call when nothing is locked.
     */
    fun exitLockTask(activity: Activity) {
        if (isLocked(activity)) {
            guarded("stopLockTask") { activity.stopLockTask() }
        }
        if (!isDeviceOwner(activity)) return
        val policy = policy(activity) ?: return
        val admin = adminOf(activity)
        guarded("setLockTaskPackages(empty)") { policy.setLockTaskPackages(admin, emptyArray()) }
        guarded("setStatusBarDisabled(false)") { policy.setStatusBarDisabled(admin, false) }
        guarded("setKeyguardDisabled(false)") { policy.setKeyguardDisabled(admin, false) }
    }

    private fun policy(context: Context): DevicePolicyManager? =
        context.getSystemService(DevicePolicyManager::class.java)
            ?: run {
                Log.w(TAG, "no DevicePolicyManager on this device")
                null
            }

    private fun adminOf(context: Context): ComponentName =
        ComponentName(context, SaathiAdminReceiver::class.java)

    private fun homeFilter(): IntentFilter =
        IntentFilter(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }

    // Every DevicePolicyManager call throws SecurityException when the
    // caller is not the owner, and a few throw IllegalArgumentException
    // for a flag the device refuses. A kiosk that cannot be entered is a
    // log line; a crash would take the face with it.
    private inline fun guarded(what: String, block: () -> Unit) {
        try {
            block()
        } catch (e: RuntimeException) {
            Log.w(TAG, "$what refused", e)
        }
    }

    private inline fun <T> guarded(what: String, fallback: T, block: () -> T): T =
        try {
            block()
        } catch (e: RuntimeException) {
            Log.w(TAG, "$what refused", e)
            fallback
        }
}
