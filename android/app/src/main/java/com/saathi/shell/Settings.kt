/**
 * What the setup person chose, kept across reboots.
 *
 * Why this file exists: two values outlive the process -- where the
 * engine is, and whether the tablet is a kiosk -- and they are the only
 * things about this shell that differ from one household to the next.
 * SharedPreferences, because two keys do not justify a database and a
 * file in app storage survives everything short of a factory reset,
 * which on a Device Owner tablet is the one way to change owner anyway.
 *
 * What lost: putting the engine address in the APK at build time. That
 * would have made every household a rebuild and every Wi-Fi change a
 * visit. Also lost: discovering the engine with mDNS. It belongs here
 * eventually (CLAUDE.md: devices are detected, not named), but a
 * discovery that fails silently on a Wi-Fi that blocks multicast is
 * worse than a dialog, so the dialog comes first and discovery can
 * pre-fill it later. The address rule itself is `EngineAddress.kt`, pure
 * Kotlin, so it is tested without a device.
 */
package com.saathi.shell

import android.content.Context
import android.content.SharedPreferences

class Settings(context: Context) {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /**
     * The engine's base URL, e.g. `http://192.168.1.10:8765`, with no
     * trailing slash, or null until the setup dialog (long-press the
     * face for five seconds) has saved one. Null, not
     * [EngineAddress.DEFAULT]: a shell that connected to the placeholder
     * address before anyone typed one was found in review talking to
     * whatever stranger device holds that very common DHCP address, so
     * the placeholder is the dialog's hint and nothing else. Use
     * [setEngineUrlIfValid] from user input; this setter trusts its caller.
     */
    var engineUrl: String?
        get() = prefs.getString(KEY_ENGINE_URL, null)
        set(value) {
            val editor = prefs.edit()
            if (value == null) editor.remove(KEY_ENGINE_URL) else editor.putString(KEY_ENGINE_URL, value)
            editor.apply()
        }

    /** Whether the shell pins itself to the screen (lock task) when it is Device Owner. */
    var kiosk: Boolean
        get() = prefs.getBoolean(KEY_KIOSK, false)
        set(value) = prefs.edit().putBoolean(KEY_KIOSK, value).apply()

    /** Store [raw] if [EngineAddress.normalise] accepts it; the stored form, or null and nothing changed. */
    fun setEngineUrlIfValid(raw: String): String? {
        val url = EngineAddress.normalise(raw) ?: return null
        engineUrl = url
        return url
    }

    companion object {
        const val FILE = "saathi"
        const val KEY_ENGINE_URL = "engine_url"
        const val KEY_KIOSK = "kiosk"
    }
}
