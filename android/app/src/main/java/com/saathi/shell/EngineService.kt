/**
 * The foreground service that keeps the microphone and the wake lock
 * across the moments the activity is paused but still on screen.
 *
 * Why this file exists: on API 30+ an app that is not in the foreground
 * loses its microphone, and "foreground" for the mic means a resumed
 * activity or a foreground service with the `microphone` type. The
 * activity is paused, not stopped, for the permission prompt, an
 * incoming call's heads-up, the HOME chooser and any system dialog --
 * and a hold that spans one of those must keep its capture. That is
 * what this service is for, and all it is for: it runs from the
 * activity's `onStart` to its `onStop`, so a screen that goes off or a
 * Home press ends it with the activity. The first draft's header
 * claimed it kept the mic open "through a dimmed screen and a paused
 * activity" for a kiosk's whole life; it never did, because `onStop`
 * stops it (found in review), and push-to-talk needs the screen on
 * anyway (`FLAG_KEEP_SCREEN_ON` in `MainActivity`). The partial wake
 * lock has the same span: the CPU must not doze between her sentences
 * and drop the sockets' pings while the face is up.
 *
 * The notification: Android requires every foreground service to show
 * one, and from API 33 the system shows an app's microphone use in the
 * status bar regardless. Its title is "Saathi" -- what it is, not a
 * state (the first draft's "Saathi is listening" was a status label and
 * a false one most of the time: the mic is open only between press and
 * release) -- in the shade, not under the face, where CLAUDE.md forbids
 * status text; the face itself shows nothing. The channel is
 * low-importance so it never sounds or pops, and a tap on it opens the
 * face. Without `POST_NOTIFICATIONS` (API 33+, not requested) the
 * notification is not shown in the shade; the service still runs.
 *
 * What lost: doing all of this in the activity (its capture is cut the
 * moment it leaves the foreground); a bound service (binding ties the
 * service's life to the binder, which is the activity's, which is the
 * problem); `START_STICKY` (a service the system restarts with no
 * activity to serve has nothing to do and a notification to explain);
 * and `startForegroundService()`. That call promises the system a
 * `startForeground()` within seconds, and a service that then cannot
 * deliver one -- API 34 refuses the `microphone` type in a few states
 * the pre-check in [start] cannot see -- is killed for the broken
 * promise, taking the shell with it: the first draft's `stopSelf()` in
 * that case was a crash, not the degrade its header described (found
 * in review). The activity is visible whenever it calls [start], so a
 * plain `startService()` is allowed there, and a refused
 * `startForeground()` is then one log line and `stopSelf()`: the face
 * stays up, the mic works while the activity is resumed, and logcat
 * says why it is no more than that.
 */
package com.saathi.shell

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat

class EngineService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (!goForeground()) {
            stopSelf()
            return
        }
        acquireWakeLock()
        Log.i(TAG, "engine service up")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY

    override fun onTaskRemoved(rootIntent: Intent?) {
        super.onTaskRemoved(rootIntent)
        stopSelf()
    }

    override fun onDestroy() {
        releaseWakeLock()
        stopForeground(STOP_FOREGROUND_REMOVE)
        Log.i(TAG, "engine service down")
        super.onDestroy()
    }

    /** Show the mandatory notification and claim the microphone type; false if the platform refused. */
    private fun goForeground(): Boolean {
        createChannel()
        val notification = buildNotification()
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            true
        } catch (e: RuntimeException) {
            // SecurityException (RECORD_AUDIO missing, API 34),
            // ForegroundServiceStartNotAllowedException (app not visible,
            // API 31+), InvalidForegroundServiceTypeException (API 34).
            Log.w(TAG, "foreground refused; the mic stops with the screen", e)
            false
        }
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel),
            NotificationManager.IMPORTANCE_LOW,
        )
        channel.setShowBadge(false)
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.notification_title))
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    // No timeout: the lock lives exactly as long as the service, and the
    // service exactly as long as the activity is started. A timeout
    // would be a second clock for the same thing.
    @SuppressLint("WakelockTimeout")
    private fun acquireWakeLock() {
        val power = getSystemService(PowerManager::class.java) ?: return
        val lock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_LOCK_TAG)
        lock.setReferenceCounted(false)
        lock.acquire()
        wakeLock = lock
    }

    private fun releaseWakeLock() {
        val lock = wakeLock ?: return
        wakeLock = null
        if (lock.isHeld) lock.release()
    }

    companion object {
        private const val TAG = "SaathiEngine"
        const val CHANNEL_ID = "engine"
        const val NOTIFICATION_ID = 1

        /** `package:tag`, the form the platform's battery screen expects. */
        const val WAKE_LOCK_TAG = "saathi:engine"

        /**
         * Whether the platform will let a `microphone` service start: from
         * API 34 only with `RECORD_AUDIO` granted; before that, always.
         * Pure, so the rule is tested without a device.
         */
        fun startAllowed(sdkInt: Int, microphoneGranted: Boolean): Boolean =
            sdkInt < Build.VERSION_CODES.UPSIDE_DOWN_CAKE || microphoneGranted

        /**
         * Start from the activity's `onStart`, and again after
         * `RECORD_AUDIO` is granted: on API 34 a start before that is one
         * log line and no service. Only while the activity is visible (a
         * plain `startService` is refused from the background). Never throws.
         */
        fun start(context: Context) {
            val granted = context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
            if (!startAllowed(Build.VERSION.SDK_INT, granted)) {
                Log.i(TAG, "RECORD_AUDIO not granted; the engine service waits for it")
                return
            }
            try {
                // startService, not startForegroundService: see the header.
                context.startService(Intent(context, EngineService::class.java))
            } catch (e: IllegalStateException) {
                // The app is not in the foreground after all (API 26+
                // refuses a background start); the next onStart tries again.
                Log.w(TAG, "engine service could not be started now", e)
            }
        }

        /** Stop from the activity's `onStop`. Nothing happens if it is not running. */
        fun stop(context: Context) {
            context.stopService(Intent(context, EngineService::class.java))
        }
    }
}
