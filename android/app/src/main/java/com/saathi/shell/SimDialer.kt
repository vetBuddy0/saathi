/**
 * [PhoneCalls.Dialer] on the device's own SIM.
 *
 * Why it exists: the one place the shell touches telephony, so the rest
 * stays pure. With `CALL_PHONE` granted the call is placed straight away
 * through `TelecomManager.placeCall`, on speaker -- she is across a table
 * from the tablet, not holding it to her ear. Without the permission it
 * falls back to the dialer with the number filled in, one tap from
 * ringing: a refused prompt must not make "call my son" do nothing.
 *
 * [canCall] needs a ready SIM *and* a voice-capable device: many LTE
 * tablets carry a SIM for data only, and saying "Calling Udhi." on one
 * of those would be a call that never rings.
 *
 * Lost: `Intent.ACTION_CALL` (no speakerphone extra; the call starts on
 * the earpiece and someone has to find the button).
 */
package com.saathi.shell

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.telecom.TelecomManager
import android.telephony.TelephonyManager
import android.util.Log
import androidx.core.content.ContextCompat

class SimDialer(private val activity: Activity) : PhoneCalls.Dialer {
    override fun canCall(): Boolean {
        if (!activity.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)) return false
        val telephony = activity.getSystemService(TelephonyManager::class.java) ?: return false
        // Many SIM tablets are data-only: a ready SIM that can't ring
        // anyone. isVoiceCapable is the device saying it can make calls.
        if (!telephony.isVoiceCapable) {
            Log.i(TAG, "SIM is data-only on this device; no calls")
            return false
        }
        return telephony.simState == TelephonyManager.SIM_STATE_READY
    }

    override fun place(number: String) {
        val uri = Uri.fromParts("tel", number, null)
        val granted = ContextCompat.checkSelfPermission(activity, Manifest.permission.CALL_PHONE) ==
            PackageManager.PERMISSION_GRANTED
        val telecom = activity.getSystemService(TelecomManager::class.java)
        if (granted && telecom != null) {
            val extras = Bundle()
            extras.putBoolean(TelecomManager.EXTRA_START_CALL_WITH_SPEAKERPHONE, true)
            try {
                telecom.placeCall(uri, extras)
                Log.i(TAG, "call placed on speaker")
                return
            } catch (e: SecurityException) {
                Log.w(TAG, "placeCall refused; opening the dialer instead", e)
            }
        }
        try {
            activity.startActivity(Intent(Intent.ACTION_DIAL, uri))
            Log.i(TAG, "no CALL_PHONE; dialer opened with the number")
        } catch (e: ActivityNotFoundException) {
            Log.e(TAG, "no dialer on this device; the call was not placed", e)
        }
    }

    private companion object {
        const val TAG = "SaathiPhone"
    }
}
