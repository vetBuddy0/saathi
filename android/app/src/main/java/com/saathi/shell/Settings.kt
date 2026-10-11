/**
 * What the setup person chose, kept across reboots -- and the keys the
 * engine inside the app is handed.
 *
 * Why this file exists: a few values outlive the process -- where the
 * engine is, whether the tablet is a kiosk, whether Saathi thinks on
 * this phone or on another computer -- and they are the only things
 * about this shell that differ from one household to the next.
 * SharedPreferences, because three keys do not justify a database and a
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
 *
 * [Keys] is the second store, and a separate one on purpose. The engine
 * in the APK (`EmbeddedEngine.kt`, `saathi/android.py`) reads its API
 * keys from a dict the shell hands it at start, never from assets or
 * resources -- an APK is readable by anyone who has it -- so the keys
 * live in the app's private storage, and in a file the Android keystore
 * encrypts (`EncryptedSharedPreferences`) rather than beside the engine
 * address: a backup, a rooted phone or a copied `shared_prefs` directory
 * then holds ciphertext without the key, which stays in hardware where
 * the phone has it. When the keystore refuses (a broken keystore on an
 * old phone, a corrupt master key after a restore -- both happen), the
 * keys fall back to a plain preferences file with one warning in the
 * log, because a phone that cannot think at all is worse than one whose
 * keys are as protected as its identity database already is. What lost:
 * refusing to run without the keystore (above). The keys come as a
 * `.env` file on the laptop the engine ran on before, so
 * [Keys.parseEnvText] reads that file -- from the clipboard, into the
 * setup dialog's one masked field per key (`SetupDialog.kt` says why
 * fields, and why a box for the whole file lost). The names are the
 * nine the engine reads and no others -- `saathi/android.py` refuses an
 * unknown name, so the shell never sends one. The parser is pure
 * Kotlin, tested on the JVM; only [Keys.open] touches the platform.
 */
package com.saathi.shell

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

class Settings(context: Context) {
    private val app: Context = context.applicationContext
    private val prefs: SharedPreferences = app.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /**
     * The engine's base URL, e.g. `http://192.168.1.10:8765`, with no
     * trailing slash, or null until the setup dialog (long-press the
     * face for five seconds) has saved one. Null, not
     * [EngineAddress.DEFAULT]: a shell that connected to the placeholder
     * address before anyone typed one was found in review talking to
     * whatever stranger device holds that very common DHCP address, so
     * the placeholder is the dialog's hint and nothing else. Use
     * [setEngineUrlIfValid] from user input; this setter trusts its caller.
     * Only read when [brainMode] is [BRAIN_REMOTE]: with the engine on
     * this phone the shell talks to `EmbeddedEngine.URL` instead.
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

    /**
     * Where Saathi thinks: [BRAIN_PHONE] (the engine inside this app, at
     * `EmbeddedEngine.URL`; the default, so a phone works with no laptop)
     * or [BRAIN_REMOTE] (an engine on the Wi-Fi at [engineUrl]). Anything
     * else stored reads as the default rather than as a third state.
     */
    var brainMode: String
        get() = brainModeOf(prefs.getString(KEY_BRAIN_MODE, null))
        set(value) = prefs.edit().putString(KEY_BRAIN_MODE, brainModeOf(value)).apply()

    /** The API keys the embedded engine is handed. Opened on first use: the keystore takes a moment. */
    val keys: Keys by lazy { Keys.open(app) }

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
        const val KEY_BRAIN_MODE = "brain_mode"

        const val BRAIN_PHONE = "phone"
        const val BRAIN_REMOTE = "remote"

        /** The mode a stored or typed value means: exactly [BRAIN_REMOTE] is remote, everything else is the phone. */
        fun brainModeOf(value: String?): String = if (value == BRAIN_REMOTE) BRAIN_REMOTE else BRAIN_PHONE
    }
}

/**
 * The engine's API keys, by the environment-variable names the engine
 * reads. See the file header for why this store exists and why it is
 * encrypted. [encrypted] says which store this is, for the log and the
 * setup dialog; the behaviour is the same either way.
 */
class Keys internal constructor(
    private val prefs: SharedPreferences,
    val encrypted: Boolean,
) {
    /** The value stored under [name] (one of [NAMES]), or null when none is, or it cannot be read back. */
    operator fun get(name: String): String? {
        if (name !in NAMES) return null
        return try {
            prefs.getString(name, null)?.takeIf { it.isNotBlank() }
        } catch (e: RuntimeException) {
            // The encrypted store throws SecurityException for an entry it
            // can no longer decrypt (a master key that changed under it).
            // The name is logged, never the value; an unreadable key is
            // "not set" until it is pasted again.
            Log.w(TAG, "$name could not be read (${e.javaClass.simpleName}); treated as not set")
            null
        }
    }

    /** Store [value] under [name]; a blank or null value removes it. False, and nothing stored, for a name the engine does not read. */
    fun set(name: String, value: String?): Boolean {
        if (name !in NAMES) return false
        val editor = prefs.edit()
        if (value.isNullOrBlank()) editor.remove(name) else editor.putString(name, value.trim())
        editor.apply()
        return true
    }

    /** Every key that has a value, by name, in [NAMES] order: what the engine is handed. */
    fun all(): Map<String, String> {
        val values = LinkedHashMap<String, String>()
        for (name in NAMES) {
            val value = get(name) ?: continue
            values[name] = value
        }
        return values
    }

    /**
     * Store what [parseEnvText] found: the names the engine reads, with a
     * blank value removing that key; every other name is ignored. Returns
     * the names stored or removed, in the order they were given, so the
     * dialog can say what it did (names, never values).
     */
    fun putAll(values: Map<String, String>): List<String> {
        val known = known(values)
        if (known.isEmpty()) return emptyList()
        val editor = prefs.edit()
        for ((name, value) in known) {
            if (value.isBlank()) editor.remove(name) else editor.putString(name, value.trim())
        }
        editor.apply()
        return known.keys.toList()
    }

    companion object {
        private const val TAG = "SaathiKeys"

        /** The encrypted preferences file, and the plain one used only when the keystore refuses. */
        const val FILE = "saathi-keys"
        const val PLAIN_FILE = "saathi-keys-plain"

        const val OPENAI_API_KEY = "OPENAI_API_KEY"
        const val GROQ_API_KEY = "GROQ_API_KEY"
        const val YOUTUBE_API_KEY = "YOUTUBE_API_KEY"
        const val GOOGLE_APPLICATION_CREDENTIALS_JSON = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        const val TWILIO_ACCOUNT_SID = "TWILIO_ACCOUNT_SID"
        const val TWILIO_API_KEY = "TWILIO_API_KEY"
        const val TWILIO_API_SECRET = "TWILIO_API_SECRET"
        const val TWILIO_FROM_NUMBER = "TWILIO_FROM_NUMBER"
        const val TWILIO_TEST_NUMBER = "TWILIO_TEST_NUMBER"

        /** The names the engine reads (`saathi/android.py` refuses any other), in the order the dialog lists them. */
        val NAMES: List<String> = listOf(
            OPENAI_API_KEY,
            GROQ_API_KEY,
            YOUTUBE_API_KEY,
            GOOGLE_APPLICATION_CREDENTIALS_JSON,
            TWILIO_ACCOUNT_SID,
            TWILIO_API_KEY,
            TWILIO_API_SECRET,
            TWILIO_FROM_NUMBER,
            TWILIO_TEST_NUMBER,
        )

        /** One of these is needed for the engine to think at all: OpenAI if set, else Groq (`provider.py`). */
        val AI_KEYS: List<String> = listOf(OPENAI_API_KEY, GROQ_API_KEY)

        private val NAME_SHAPE = Regex("[A-Za-z_][A-Za-z0-9_]*")

        /** Open the store: encrypted when the keystore allows, plain app storage (with a warning) when it does not. */
        fun open(context: Context): Keys {
            val app = context.applicationContext
            return try {
                val masterKey = MasterKey.Builder(app)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build()
                val prefs = EncryptedSharedPreferences.create(
                    app,
                    FILE,
                    masterKey,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
                )
                Keys(prefs, encrypted = true)
            } catch (e: Exception) {
                // GeneralSecurityException and IOException from the
                // library; the keystore's own RuntimeExceptions on some
                // phones. All the same here: keys in plain app storage.
                Log.w(TAG, "the keystore refused (${e.javaClass.simpleName}); keys are kept in plain app storage", e)
                Keys(app.getSharedPreferences(PLAIN_FILE, Context.MODE_PRIVATE), encrypted = false)
            }
        }

        /** Whether the engine can think with [keys]: one of [AI_KEYS] has a value. YouTube and the rest are optional. */
        fun canThink(keys: Map<String, String>): Boolean = AI_KEYS.any { !keys[it].isNullOrBlank() }

        /** The entries of [values] whose names the engine reads, in [values]' order. */
        fun known(values: Map<String, String>): Map<String, String> = values.filterKeys { it in NAMES }

        /**
         * `KEY=VALUE` lines, as in a `.env` file: a leading `export ` is
         * dropped, a value that opens with a single or double quote ends
         * at the first unescaped matching quote (dotenv's rule) and loses
         * both -- what is between them is kept verbatim, no escape
         * processing, and what follows the closing quote (an inline
         * comment) is dropped; an unquoted value ends at an inline ` #`
         * comment, blank lines and `#` lines are skipped, and a line that
         * is not `NAME=...` is ignored. The Google credential may span
         * lines: when the value after `GOOGLE_APPLICATION_CREDENTIALS_JSON=`
         * opens a `{` that the line does not close, the following lines
         * are taken until the braces balance (outside JSON strings), so a
         * service-account file pasted as-is after the `=` is one value; a
         * JSON value ends where its braces balance, quoted or not, so a
         * comment after it is not part of it (found in review: a quoted
         * key followed by a comment kept its quotes, and every call then
         * failed with a 401). Every name found is returned, known to the
         * engine or not -- [known] and [putAll] decide what is kept.
         * Later lines win over earlier ones.
         */
        fun parseEnvText(text: String): Map<String, String> {
            val result = LinkedHashMap<String, String>()
            val lines = text.lines()
            var i = 0
            while (i < lines.size) {
                var line = lines[i].trim()
                i++
                if (line.isEmpty() || line.startsWith("#")) continue
                if (line.startsWith("export ")) line = line.substring("export ".length).trim()
                val eq = line.indexOf('=')
                if (eq <= 0) continue
                val name = line.substring(0, eq).trim()
                if (!NAME_SHAPE.matches(name)) continue
                var value = line.substring(eq + 1).trim()
                if (name == GOOGLE_APPLICATION_CREDENTIALS_JSON && !jsonClosed(value)) {
                    val collected = StringBuilder(value)
                    while (i < lines.size && !jsonClosed(collected.toString())) {
                        collected.append('\n').append(lines[i])
                        i++
                    }
                    value = collected.toString().trim()
                }
                result[name] = unquote(value)
            }
            return result
        }

        /**
         * False only while [value] (after an opening quote, if any) starts
         * a JSON object whose braces have not balanced yet.
         */
        private fun jsonClosed(value: String): Boolean {
            val text = value.trim()
            val from = if (text.startsWith("\"") || text.startsWith("'")) 1 else 0
            if (from >= text.length || text[from] != '{') return true
            return jsonEnd(text, from) > 0
        }

        /**
         * The index just past the `}` that balances the `{` at [from] in
         * [text], or -1 when [from] is not a `{` or the braces never
         * balance. Braces inside JSON strings do not count; a backslash
         * escapes the next character.
         */
        private fun jsonEnd(text: String, from: Int): Int {
            if (from >= text.length || text[from] != '{') return -1
            var depth = 0
            var inString = false
            var escaped = false
            for (i in from until text.length) {
                val ch = text[i]
                if (inString) {
                    when {
                        escaped -> escaped = false
                        ch == '\\' -> escaped = true
                        ch == '"' -> inString = false
                    }
                    continue
                }
                when (ch) {
                    '"' -> inString = true
                    '{' -> depth += 1
                    '}' -> {
                        depth -= 1
                        if (depth == 0) return i + 1
                    }
                }
            }
            return -1
        }

        /**
         * The index of the quote that closes the one at index 0 of
         * [value], or -1 when none does: the first unescaped matching
         * quote, except that a JSON object right after the opening quote
         * is taken whole first (its own `"` are content) and the closing
         * quote is looked for after it.
         */
        private fun closingQuote(value: String): Int {
            val quote = value[0]
            if (value.length > 1 && value[1] == '{') {
                val end = jsonEnd(value, 1)
                if (end > 0) {
                    var i = end
                    while (i < value.length && value[i].isWhitespace()) i += 1
                    return if (i < value.length && value[i] == quote) i else -1
                }
            }
            var i = 1
            while (i < value.length) {
                when (value[i]) {
                    '\\' -> i += 1
                    quote -> return i
                }
                i += 1
            }
            return -1
        }

        private fun unquote(raw: String): String {
            val value = raw.trim()
            if (value.isEmpty()) return value
            if (value[0] == '"' || value[0] == '\'') {
                // Quoted: up to the closing quote, whatever follows it
                // dropped; with no closing quote, kept as it is.
                val close = closingQuote(value)
                return if (close > 0) value.substring(1, close) else value
            }
            if (value[0] == '{') {
                // JSON: a `#` inside it is content; a comment after its
                // braces balance is not.
                val end = jsonEnd(value, 0)
                return if (end > 0) value.substring(0, end) else value
            }
            val comment = value.indexOf(" #")
            return if (comment >= 0) value.substring(0, comment).trim() else value
        }

        private val CLIENT_EMAIL = Regex("\"client_email\"\\s*:\\s*\"([^\"]+)\"")

        /**
         * What a pasted Google credential looks like, for a field that
         * shows dots (`SetupDialog.kt`): whether it is one JSON object
         * whose braces balance with nothing after it, and whose service
         * account it names (`client_email`), so a missed brace or the
         * wrong file is caught without the private key on screen beside
         * it. A glance, not a parse: the engine parses it for real
         * (`saathi/android.py`) and says so in its notes.
         */
        fun credentialShape(text: String): CredentialShape {
            val trimmed = text.trim()
            val balanced = trimmed.startsWith("{") && jsonEnd(trimmed, 0) == trimmed.length
            val email = CLIENT_EMAIL.find(trimmed)?.groupValues?.get(1)
            return CredentialShape(balanced, email)
        }
    }
}

/** [Keys.credentialShape]'s answer: one balanced JSON object or not, and the `client_email` in it, if any. */
data class CredentialShape(val balanced: Boolean, val clientEmail: String?)
