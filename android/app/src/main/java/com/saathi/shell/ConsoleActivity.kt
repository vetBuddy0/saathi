/**
 * The developer console (`screen/admin.py`'s `/admin`) on the tablet,
 * full screen, opened from the setup dialog -- never from her face.
 *
 * Why its own activity: the console is a page for whoever sets Saathi up
 * (memory, personality, contacts, keys), and the face WebView must stay
 * the face. Why a bridge: the API keys live in this shell's encrypted
 * store ([Keys]), not in the engine, so the console's Keys tab writes
 * them through [Bridge] -- status by name and a save, never a value read
 * back. Saving marks [keysChanged] and closes; [MainActivity] then
 * restarts the engine with the new keys, the same path the setup
 * dialog's Save takes.
 *
 * The bridge is offered only to the engine's own page: a navigation
 * anywhere else is refused, so no other page ever sees `SaathiShell`.
 * Lost: re-implementing memory and contacts natively (a second UI to
 * keep in step with the engine; the laptop's browser would not have it).
 */
package com.saathi.shell

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

class ConsoleActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private lateinit var engine: String

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        engine = intent.getStringExtra(EXTRA_ENGINE)?.trimEnd('/') ?: run {
            finish()
            return
        }
        web = WebView(this)
        web.setBackgroundColor(Color.parseColor("#f6f3ee"))
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.allowFileAccess = false
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val ours = request.url.toString().startsWith("$engine/admin")
                if (!ours) Log.w(TAG, "console stays on the engine's page; refused ${request.url.host}")
                return !ours
            }
        }
        web.addJavascriptInterface(Bridge(Keys.open(this)), "SaathiShell")
        setContentView(web)
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (web.canGoBack()) web.goBack() else finish()
                }
            },
        )
        web.loadUrl("$engine/admin")
    }

    override fun onDestroy() {
        if (::web.isInitialized) {
            web.removeJavascriptInterface("SaathiShell")
            web.destroy()
        }
        super.onDestroy()
    }

    /** What the console page may ask of the shell. Called on a WebView thread. */
    inner class Bridge(private val keys: Keys) {
        /** `{"NAME": true|false}` for every key, in the dialog's order. Never a value. */
        @JavascriptInterface
        fun keyStatus(): String {
            val stored = keys.all()
            val status = JSONObject()
            for (name in Keys.NAMES) status.put(name, stored.containsKey(name))
            return status.toString()
        }

        /**
         * Store `{"NAME": "value"}` for the names the engine reads (others
         * ignored), then close so the engine restarts with them. Answers
         * `{"stored": [...]}` or `{"error": "..."}`.
         */
        @JavascriptInterface
        fun saveKeys(json: String): String {
            val values = try {
                val obj = JSONObject(json)
                obj.keys().asSequence().associateWith { obj.optString(it) }
            } catch (e: JSONException) {
                return JSONObject().put("error", "not a JSON object").toString()
            }
            val known = Keys.known(values).filterValues { it.isNotBlank() }
            if (known.isEmpty()) return JSONObject().put("error", "no key the engine reads").toString()
            if (Keys.AI_KEYS.none { !(keys[it] ?: known[it]).isNullOrBlank() }) {
                return JSONObject().put("error", "OPENAI_API_KEY or GROQ_API_KEY is needed").toString()
            }
            val stored = keys.putAll(known)
            Log.i(TAG, "console stored ${stored.joinToString()}")
            keysChanged = true
            web.postDelayed({ finish() }, CLOSE_AFTER_SAVE_MS)
            return JSONObject().put("stored", JSONArray(stored)).toString()
        }
    }

    companion object {
        private const val TAG = "SaathiConsole"
        private const val EXTRA_ENGINE = "engine"
        private const val CLOSE_AFTER_SAVE_MS = 1500L

        /** Set by a save; [MainActivity] restarts the engine on its next resume and clears it. */
        @Volatile
        var keysChanged: Boolean = false

        fun open(activity: Activity, engineUrl: String) {
            activity.startActivity(Intent(activity, ConsoleActivity::class.java).putExtra(EXTRA_ENGINE, engineUrl))
        }
    }
}
