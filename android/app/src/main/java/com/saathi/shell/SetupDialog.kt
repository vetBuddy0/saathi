/**
 * The one screen that is not hers: where the engine is, and whether this
 * tablet is a kiosk.
 *
 * Why this file exists: somebody has to type the engine's address once,
 * and a settings activity, a menu or a gear icon would all put a control
 * on her screen that is not for her. A five-second hold on the face is
 * nothing she would do by accident and nothing she needs to know about;
 * it is for whoever sets the device up, like the Ctrl+L panel on the
 * face page. Built from plain widgets in code rather than a layout file
 * because it is one field, one button and one checkbox, and a layout
 * file for that is a second place to keep them.
 *
 * "Test" does one GET of the address's root with a short timeout and
 * says whether the engine answered, before anything is saved, because
 * the alternative is saving a typo and watching a black face with no
 * way to tell a wrong address from a down engine. Save stores the
 * address, points both links at it ([EngineLink] and [AudioLink]
 * reconnect to whatever they are given), applies the kiosk box through
 * [Kiosk], then calls back so the activity can reload the face. "Exit
 * kiosk" is offered only when the app is not Device Owner: on the demo
 * phone a pinned screen with the system bars hidden has no other way
 * out, while on the owner tablet leaving the kiosk is the checkbox's
 * job, and a one-tap exit on the screen a curious grandchild can reach
 * with a five-second hold is not something the tablet should have.
 *
 * What lost: a long-click listener (fires at the system's ~500 ms, which
 * a resting thumb trips), a hidden tap sequence (a corner tapped five
 * times is exactly what a curious grandchild finds first), and the
 * links' own OkHttp client for the test. The test wants a three-second
 * timeout and the links want none (a WebSocket upgrade with a read
 * timeout dies on the first quiet minute), so the probe has a small
 * client of its own, made once.
 */
package com.saathi.shell

import android.app.Activity
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response

object SetupDialog {
    /** How long the face must be held before the dialog opens. */
    const val HOLD_MS = 5000L

    /** The probe gives up after this; a LAN engine answers in milliseconds or not at all. */
    const val PROBE_TIMEOUT_SECONDS = 3L

    // Lazy, not eager: the object must load on a plain JVM for the tests
    // of its pure parts, and a Handler needs a Looper.
    private val main: Handler by lazy { Handler(Looper.getMainLooper()) }

    private val probeClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(PROBE_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .readTimeout(PROBE_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .callTimeout(PROBE_TIMEOUT_SECONDS * 2, TimeUnit.SECONDS)
            .build()
    }

    /** As [show] with links, for a caller that reconnects in [onSaved] itself. */
    fun show(activity: Activity, settings: Settings, onSaved: () -> Unit) {
        show(activity, settings, null, null, onSaved)
    }

    /**
     * Open the dialog. On Save, [engineLink] and [audioLink] (either may
     * be null) are pointed at the stored address before [onSaved] runs.
     */
    fun show(
        activity: Activity,
        settings: Settings,
        engineLink: EngineLink?,
        audioLink: AudioLink?,
        onSaved: () -> Unit,
    ) {
        val context: Context = activity
        val padding = (16 * context.resources.displayMetrics.density).toInt()
        val urlField = EditText(context).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(settings.engineUrl ?: "")
            hint = EngineAddress.DEFAULT
            setSingleLine(true)
        }
        val verdict = TextView(context)
        val testButton = Button(context).apply {
            text = context.getString(R.string.setup_test)
            setOnClickListener { probe(urlField.text.toString(), this, verdict) }
        }
        val addressRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(urlField, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(testButton)
        }
        val kioskBox = CheckBox(context).apply {
            text = context.getString(R.string.setup_kiosk)
            isChecked = settings.kiosk
        }
        val column = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, 0)
            addView(addressRow)
            addView(verdict)
            addView(kioskBox)
        }
        val builder = AlertDialog.Builder(context)
            .setTitle(R.string.setup_title)
            .setMessage(R.string.setup_message)
            .setView(column)
            .setNegativeButton(R.string.setup_cancel, null)
            .setPositiveButton(R.string.setup_save) { _, _ ->
                val stored = settings.setEngineUrlIfValid(urlField.text.toString())
                if (stored == null) {
                    Toast.makeText(context, R.string.setup_rejected, Toast.LENGTH_LONG).show()
                } else {
                    settings.kiosk = kioskBox.isChecked
                    engineLink?.connect(stored)
                    audioLink?.connect(stored)
                    if (settings.kiosk) Kiosk.enterLockTaskIfOwner(activity) else Kiosk.exitLockTask(activity)
                    onSaved()
                }
            }
        if (!Kiosk.isDeviceOwner(context)) {
            builder.setNeutralButton(R.string.setup_exit_kiosk) { _, _ ->
                settings.kiosk = false
                Kiosk.exitLockTask(activity)
            }
        }
        builder.show()
    }

    /**
     * What "Test" fetches for what was typed: the engine's root, with a
     * trailing slash, or null when the address is not one this shell
     * would store (same rule as Save, so the test cannot pass for an
     * address Save then refuses).
     */
    fun probeUrl(raw: String): String? = EngineAddress.normalise(raw)?.let { "$it/" }

    /** Whether an HTTP status counts as the engine answering: any 2xx. */
    fun probeOk(status: Int): Boolean = status in 200..299

    private fun probe(raw: String, button: Button, verdict: TextView) {
        val url = probeUrl(raw)
        if (url == null) {
            verdict.setText(R.string.setup_rejected)
            return
        }
        button.isEnabled = false
        verdict.text = ""
        val request = Request.Builder().url(url).get().build()
        probeClient.newCall(request).enqueue(
            object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    report(button, verdict, false, e.message ?: e.javaClass.simpleName)
                }

                override fun onResponse(call: Call, response: Response) {
                    val status = response.code
                    response.close()
                    report(button, verdict, probeOk(status), "HTTP $status")
                }
            },
        )
    }

    // OkHttp calls back on its own thread; the views are the main
    // thread's. If the dialog has gone by then, the views are detached
    // and the update is harmless.
    private fun report(button: Button, verdict: TextView, ok: Boolean, detail: String) {
        main.post {
            button.isEnabled = true
            val id = if (ok) R.string.setup_test_ok else R.string.setup_test_fail
            verdict.text = verdict.context.getString(id, detail)
        }
    }
}

/**
 * Fires [onHold] once a finger has rested on the view for [holdMs]
 * without moving more than the touch slop. Returns false from every
 * event so the view underneath (a WebView) still receives the touch.
 * [cancel] is for the activity's `onPause`/`onDestroy`: a hold that is
 * down when the activity goes (a renderer crash recreating it) would
 * otherwise fire into a finished activity and open the dialog on a
 * window that no longer exists (found in review).
 */
class HoldListener(private val holdMs: Long, private val onHold: () -> Unit) : View.OnTouchListener {
    private val handler = Handler(Looper.getMainLooper())
    private val fire = Runnable { onHold() }
    private var downX = 0f
    private var downY = 0f

    /** Forget a hold in progress; nothing happens if none is. */
    fun cancel() {
        handler.removeCallbacks(fire)
    }

    override fun onTouch(view: View, event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x
                downY = event.y
                handler.removeCallbacks(fire)
                handler.postDelayed(fire, holdMs)
            }
            MotionEvent.ACTION_MOVE -> {
                val slop = ViewConfiguration.get(view.context).scaledTouchSlop
                if (abs(event.x - downX) > slop || abs(event.y - downY) > slop) {
                    handler.removeCallbacks(fire)
                }
            }
            MotionEvent.ACTION_UP,
            MotionEvent.ACTION_CANCEL,
            MotionEvent.ACTION_POINTER_DOWN,
            -> handler.removeCallbacks(fire)
        }
        return false
    }
}
