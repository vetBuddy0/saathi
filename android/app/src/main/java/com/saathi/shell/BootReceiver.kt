/**
 * Brings the shell up after a reboot on the one kind of device where
 * that is both needed and possible: a phone that is not the launcher,
 * on Android 8 or 9.
 *
 * Why this file exists: a tablet that reboots overnight (an update, a
 * power cut) must show the face again without anyone touching it. On
 * the Device Owner tablet that is HOME's job -- the shell is the
 * launcher, and the system starts it; this receiver is redundant there.
 * On the demo phone the shell is one app among others, and only a boot
 * receiver can reopen it -- on API 26-28. From Android 10 (API 29) the
 * system ignores an activity start from the background by an ordinary
 * app, boot receivers included, and `RECEIVE_BOOT_COMPLETED` grants no
 * exemption; the first draft of this file called `startActivity` anyway
 * and claimed to be "the only way back", which on every current phone it
 * is not (found in review). So on API 29+ it logs that a tap is needed
 * and does nothing, and the README says the same. What lost: dropping
 * the receiver (it still serves an Android 8/9 phone, and minSdk is 26),
 * and a foreground service started here that then opens the activity --
 * the same background-start rule stops that, and a microphone service
 * may only be started while the app is visible on API 34, so the
 * activity starts `EngineService` in its own lifecycle, once it is.
 */
package com.saathi.shell

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // The owner tablet is HOME and is already up; a plain app
            // cannot start an activity from here on Android 10+.
            Log.i(TAG, "boot completed; Android 10+ needs a tap on the icon unless Saathi is HOME")
            return
        }
        Log.i(TAG, "boot completed; opening the face")
        val launch = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(launch)
    }

    private companion object {
        const val TAG = "SaathiKiosk"
    }
}
