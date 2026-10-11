/**
 * The one screen that is not hers: where Saathi thinks, the address of
 * an engine elsewhere, the keys for the engine in the app, and whether
 * this tablet is a kiosk.
 *
 * Why this file exists: somebody has to set the device up once, and a
 * settings activity, a menu or a gear icon would all put a control on
 * her screen that is not for her. A five-second hold on the face is
 * nothing she would do by accident and nothing she needs to know about;
 * it is for whoever sets the device up, like the Ctrl+L panel on the
 * face page. Built from plain widgets in code rather than a layout file
 * because the Keys page is one row per name in [Keys.NAMES] -- a loop
 * here, nine copies of the row to keep in step with the list there.
 *
 * Two pages under one Save, because the two jobs are rarely both needed.
 * The Brain page is the choice -- "Saathi thinks on this phone" (the
 * engine in the app, the default) or "on another computer" -- with the
 * address and "Test" under the second choice. The Keys page is one
 * masked field per key the engine reads, a plain multi-line box for the
 * Google service-account JSON, a "Clear" beside each, and "Paste .env
 * from clipboard", which runs the clipboard through [Keys.parseEnvText]
 * and fills the fields by name. The activity opens the dialog on Keys
 * when the phone is to think for itself and has no AI key yet, on Brain
 * when it is to use an address and has none; the hold opens it on Brain.
 *
 * Nothing stored is ever shown back. Every field is empty when the
 * dialog opens and its label says only "set" or "missing"; a value typed
 * or pasted replaces the stored one on Save, "Clear" takes it out, and
 * an empty field changes nothing -- which is why "empty" cannot mean
 * "remove" here and Clear exists. The fields are masked, so a key pasted
 * in a living room is a row of dots -- the JSON box too, since
 * 2026-10-08: it was the one plain field, because a masked multi-line
 * box cannot be checked for a missed brace, and a review pointed out
 * that it showed the service account's private key in clear, in every
 * screenshot and Recents thumbnail. The check it existed for is done
 * for the person instead: a line under the box says whether what is in
 * it is one JSON object whose braces balance, and which service account
 * it names ([Keys.credentialShape]), which is more than eyes on a
 * private key could tell. The dialog's window is `FLAG_SECURE` for the
 * same reason, so the system's screenshot, screen recording and Recents
 * get a blank where it is. After "Paste .env from clipboard" has filled
 * the fields, the clipboard is emptied: the whole file, every key in it,
 * was otherwise left for the next app with focus to read. [KeyEdits]
 * holds what the page will do on Save -- pure Kotlin, tested on the JVM
 * -- and the widgets only show it. Nothing here logs or toasts a value:
 * the toasts name keys.
 *
 * "Test" does one GET of the address's root with a short timeout and
 * says whether the engine answered, before anything is saved, because
 * the alternative is saving a typo and watching a black face with no
 * way to tell a wrong address from a down engine. It follows no
 * redirect: a LAN address answering 302 to a public host would
 * otherwise have the probe fetch that host (found in review), and the
 * engine does not redirect; a 3xx is "not an answer". Save stores what
 * changed (the address, the keys, the mode, the kiosk box), applies the
 * kiosk box through [Kiosk], and -- when the engine is remote and links
 * were given -- points [EngineLink] and [AudioLink] at the stored
 * address, then calls back so the activity can act on the mode: with
 * the engine on the phone, `MainActivity.applyBrain()` hands the stored
 * keys to `EmbeddedEngine`, which restarts the engine when they changed,
 * and connects the links once it answers -- a thing this dialog cannot
 * know. Save keeps the dialog open, with everything typed still in it,
 * when the address is refused or when the phone is to think and would
 * hold no AI key: a dialog that closed on a typo threw away a page of
 * pasted keys with it. "Exit kiosk" is offered only when the app is not
 * Device Owner: on the demo phone a pinned screen with the system bars
 * hidden has no other way out, while on the owner tablet leaving the
 * kiosk is the checkbox's job, and a one-tap exit on the screen a
 * curious grandchild can reach with a five-second hold is not something
 * the tablet should have.
 *
 * What lost: a long-click listener (fires at the system's ~500 ms, which
 * a resting thumb trips), a hidden tap sequence (a corner tapped five
 * times is exactly what a curious grandchild finds first), and the
 * links' own OkHttp client for the test. The test wants a three-second
 * timeout and the links want none (a WebSocket upgrade with a read
 * timeout dies on the first quiet minute), so the probe has a small
 * client of its own, made once. Also lost, and it was the first Keys
 * page: one multi-line box for the whole `.env` file and nothing else.
 * It showed every key in clear for as long as it sat there, a person
 * with one new key re-pasted the whole file to be sure which name it
 * landed under, and nothing on the page said, per key, what the phone
 * already held. The file can still be pasted once -- from the
 * clipboard, into the fields by name, a blank `NAME=` clearing that key
 * as it did before -- and one key goes into its own field. A box on the
 * page to paste the file into as well as the clipboard button lost as a
 * second place to paste the same thing; showing stored keys masked lost
 * because a masked key cannot be checked anyway, and "set" is what a
 * person needs to know.
 */
package com.saathi.shell

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.DialogInterface
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.text.method.PasswordTransformationMethod
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
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

    /** Which page the dialog opens on: where Saathi thinks (and the address), or the keys. */
    enum class Page { BRAIN, KEYS }

    // Lazy, not eager: the object must load on a plain JVM for the tests
    // of its pure parts, and a Handler needs a Looper.
    private val main: Handler by lazy { Handler(Looper.getMainLooper()) }

    private val probeClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(PROBE_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .readTimeout(PROBE_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .callTimeout(PROBE_TIMEOUT_SECONDS * 2, TimeUnit.SECONDS)
            // The engine does not redirect, and a redirect from a LAN
            // address to a public host must not be fetched (the header).
            .followRedirects(false)
            .followSslRedirects(false)
            .build()
    }

    /** As [show] with links, on the Brain page, for a caller that connects in [onSaved] itself. */
    fun show(activity: Activity, settings: Settings, onSaved: () -> Unit) {
        show(activity, settings, null, null, Page.BRAIN, onSaved)
    }

    /** As [show] with links, on [page], for a caller that connects in [onSaved] itself. */
    fun show(activity: Activity, settings: Settings, page: Page, onSaved: () -> Unit) {
        show(activity, settings, null, null, page, onSaved)
    }

    /** As [show] with a page, opening on the Brain page. */
    fun show(
        activity: Activity,
        settings: Settings,
        engineLink: EngineLink?,
        audioLink: AudioLink?,
        onSaved: () -> Unit,
    ) {
        show(activity, settings, engineLink, audioLink, Page.BRAIN, onSaved)
    }

    /**
     * Open the dialog on [page]. On Save, when the engine is remote,
     * [engineLink] and [audioLink] (either may be null) are pointed at
     * the stored address before [onSaved] runs; when the engine is on
     * the phone they are left alone and [onSaved] decides.
     */
    fun show(
        activity: Activity,
        settings: Settings,
        engineLink: EngineLink?,
        audioLink: AudioLink?,
        page: Page,
        onSaved: () -> Unit,
    ) {
        val context: Context = activity
        val density = context.resources.displayMetrics.density
        val padding = (16 * density).toInt()
        val indent = (32 * density).toInt()
        fun say(text: CharSequence) = Toast.makeText(context, text, Toast.LENGTH_LONG).show()

        // The Brain page: the choice, and under "another computer" the address and Test.
        val phoneChoice = RadioButton(context).apply {
            id = View.generateViewId()
            text = context.getString(R.string.setup_brain_phone)
        }
        val remoteChoice = RadioButton(context).apply {
            id = View.generateViewId()
            text = context.getString(R.string.setup_brain_remote)
        }
        val brain = RadioGroup(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(phoneChoice)
            addView(remoteChoice)
            check(if (settings.brainMode == Settings.BRAIN_REMOTE) remoteChoice.id else phoneChoice.id)
        }
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
        val addressBlock = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            // Indented under the second choice: the address is that choice's.
            setPadding(indent, 0, 0, 0)
            addView(TextView(context).apply { setText(R.string.setup_message) })
            addView(addressRow)
            addView(verdict)
        }
        val brainPage = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(brain)
            addView(addressBlock)
        }

        // The Keys page: the paste button, then one row per name the engine
        // reads -- its label (set or missing) and Clear, and its field.
        val edits = KeyEdits(settings.keys.all().keys)
        val fields = LinkedHashMap<String, EditText>()
        val labels = LinkedHashMap<String, TextView>()
        val clears = LinkedHashMap<String, Button>()
        // Under the (masked) JSON box: what is in it looks like, so a
        // missed brace or the wrong file is caught without showing it.
        val jsonVerdict = TextView(context)
        fun refresh(name: String) {
            val held = edits.willHave(name)
            labels.getValue(name).text =
                context.getString(if (held) R.string.setup_key_set else R.string.setup_key_missing, name)
            clears.getValue(name).isEnabled = held
            if (name == Keys.GOOGLE_APPLICATION_CREDENTIALS_JSON) {
                jsonVerdict.text = credentialVerdict(context, edits.typedValue(name))
            }
        }
        val pasteButton = Button(context).apply { text = context.getString(R.string.setup_keys_paste) }
        val keysPage = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(context).apply { setText(R.string.setup_keys_message) })
            addView(pasteButton)
        }
        for (name in Keys.NAMES) {
            val label = TextView(context)
            val clear = Button(context).apply { text = context.getString(R.string.setup_key_clear) }
            val field = EditText(context).apply {
                if (name == Keys.GOOGLE_APPLICATION_CREDENTIALS_JSON) {
                    inputType = InputType.TYPE_CLASS_TEXT or
                        InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                        InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
                    // Masked after the input type is set: a multi-line
                    // password *type* is not a thing, a multi-line field
                    // with the password transformation is. setInputType
                    // would reset the transformation if it came later.
                    transformationMethod = PasswordTransformationMethod.getInstance()
                    hint = context.getString(R.string.setup_key_json_hint)
                    minLines = 3
                    gravity = Gravity.TOP or Gravity.START
                } else {
                    inputType = InputType.TYPE_CLASS_TEXT or
                        InputType.TYPE_TEXT_VARIATION_PASSWORD or
                        InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
                    hint = context.getString(R.string.setup_key_hint)
                    setSingleLine(true)
                }
            }
            labels[name] = label
            clears[name] = clear
            fields[name] = field
            field.addTextChangedListener(
                object : TextWatcher {
                    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit

                    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit

                    override fun afterTextChanged(s: Editable?) {
                        edits.type(name, s?.toString() ?: "")
                        refresh(name)
                    }
                },
            )
            clear.setOnClickListener {
                edits.clear(name)
                field.setText("") // the watcher reads it back: typed is gone, cleared stays
                refresh(name)
            }
            keysPage.addView(
                LinearLayout(context).apply {
                    orientation = LinearLayout.HORIZONTAL
                    setGravity(Gravity.CENTER_VERTICAL)
                    addView(label, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
                    addView(clear)
                },
            )
            keysPage.addView(field)
            if (name == Keys.GOOGLE_APPLICATION_CREDENTIALS_JSON) keysPage.addView(jsonVerdict)
            refresh(name)
        }
        pasteButton.setOnClickListener {
            val text = clipboardText(context)
            if (text.isBlank()) {
                say(context.getString(R.string.setup_keys_clipboard_empty))
                return@setOnClickListener
            }
            val touched = edits.fill(Keys.parseEnvText(text))
            for (name in touched) {
                // Shown masked, and read back by the watcher into the same state.
                fields.getValue(name).setText(edits.typedValue(name) ?: "")
                refresh(name)
            }
            if (touched.isEmpty()) {
                say(context.getString(R.string.setup_keys_none_found))
            } else {
                // The file has done its job; it does not stay on the
                // clipboard for the next app with focus (the header).
                clearClipboard(context)
                say(context.getString(R.string.setup_keys_filled, touched.joinToString(", ")))
            }
        }

        // The two pages, one shown at a time; the tab of the shown page is the disabled one.
        val brainTab = Button(context).apply { text = context.getString(R.string.setup_page_brain) }
        val keysTab = Button(context).apply { text = context.getString(R.string.setup_page_keys) }
        fun open(shown: Page) {
            brainPage.visibility = if (shown == Page.BRAIN) View.VISIBLE else View.GONE
            keysPage.visibility = if (shown == Page.KEYS) View.VISIBLE else View.GONE
            brainTab.isEnabled = shown != Page.BRAIN
            keysTab.isEnabled = shown != Page.KEYS
        }
        brainTab.setOnClickListener { open(Page.BRAIN) }
        keysTab.setOnClickListener { open(Page.KEYS) }
        val tabs = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(brainTab, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(keysTab, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        }
        open(page)

        val kioskBox = CheckBox(context).apply {
            text = context.getString(R.string.setup_kiosk)
            isChecked = settings.kiosk
        }
        val column = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, 0)
            addView(tabs)
            addView(brainPage)
            addView(keysPage)
            addView(kioskBox)
        }
        // A pasted credential is long and the screen is a phone's in
        // landscape: the body scrolls, the buttons stay.
        val body = ScrollView(context).apply { addView(column) }

        // True when everything was stored and the dialog may close; false
        // with a toast saying why not, the page that needs attention
        // shown, and nothing stored.
        fun save(): Boolean {
            val remote = brain.checkedRadioButtonId == remoteChoice.id
            if (!remote && !edits.canThink()) {
                open(Page.KEYS)
                say(context.getString(R.string.setup_keys_need_ai))
                return false
            }
            val typedAddress = urlField.text.toString()
            // The address is required for a remote engine and optional,
            // but still checked, for the phone's own.
            val wantsAddress = remote || typedAddress.isNotBlank()
            val stored = if (wantsAddress) settings.setEngineUrlIfValid(typedAddress) else null
            if (wantsAddress && stored == null) {
                open(Page.BRAIN)
                say(context.getString(R.string.setup_rejected))
                return false
            }
            val changes = edits.changes()
            if (changes.isNotEmpty()) {
                settings.keys.putAll(changes)
                // Names, never values.
                val put = changes.filterValues { it.isNotBlank() }.keys
                val removed = changes.filterValues { it.isBlank() }.keys
                val said = StringBuilder()
                if (put.isNotEmpty()) {
                    said.append(context.getString(R.string.setup_keys_stored, put.joinToString(", ")))
                }
                if (removed.isNotEmpty()) {
                    if (said.isNotEmpty()) said.append(' ')
                    said.append(context.getString(R.string.setup_keys_removed, removed.joinToString(", ")))
                }
                say(said)
            }
            settings.brainMode = if (remote) Settings.BRAIN_REMOTE else Settings.BRAIN_PHONE
            settings.kiosk = kioskBox.isChecked
            if (remote && stored != null) {
                engineLink?.connect(stored)
                audioLink?.connect(stored)
            }
            if (settings.kiosk) Kiosk.enterLockTaskIfOwner(activity) else Kiosk.exitLockTask(activity)
            onSaved()
            return true
        }

        val builder = AlertDialog.Builder(context)
            .setTitle(R.string.setup_title)
            .setView(body)
            .setNegativeButton(R.string.setup_cancel, null)
            // Wired below, after show(): a listener given to the builder
            // has the dialog dismissed whatever it decided, and a refused
            // Save must leave the pages as they were.
            .setPositiveButton(R.string.setup_save, null)
        if (!Kiosk.isDeviceOwner(context)) {
            builder.setNeutralButton(R.string.setup_exit_kiosk) { _, _ ->
                settings.kiosk = false
                Kiosk.exitLockTask(activity)
            }
        }
        val dialog = builder.create()
        // No screenshot, screen recording or Recents thumbnail of a
        // window that can hold a private key (the header). Set before
        // show(), when the window is created but not yet attached.
        dialog.window?.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        dialog.show()
        dialog.getButton(DialogInterface.BUTTON_POSITIVE)?.setOnClickListener {
            if (save()) dialog.dismiss()
        }
    }

    /**
     * The line under the JSON box for what [typed] holds: nothing for an
     * empty box; which service account and that the braces balance; or
     * that they do not. Names the account, never the key.
     */
    private fun credentialVerdict(context: Context, typed: String?): CharSequence {
        if (typed == null) return ""
        val shape = Keys.credentialShape(typed)
        if (!shape.balanced) return context.getString(R.string.setup_key_json_unbalanced)
        val account = shape.clientEmail ?: context.getString(R.string.setup_key_json_no_email)
        return context.getString(R.string.setup_key_json_ok, account)
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

    /**
     * What the clipboard holds, as text, or "" when it holds nothing
     * readable. Android 10 and later hand the clipboard only to the app
     * with input focus, which a press on this dialog's own button is.
     * The text is parsed and never logged.
     */
    private fun clipboardText(context: Context): String {
        val clipboard = context.getSystemService(ClipboardManager::class.java) ?: return ""
        val clip = clipboard.primaryClip ?: return ""
        if (clip.itemCount == 0) return ""
        return clip.getItemAt(0).coerceToText(context).toString()
    }

    /**
     * Take the pasted file off the clipboard: every key in it, the ones
     * the engine reads or not, was otherwise readable by the next app
     * with focus. `clearPrimaryClip` is API 28; before it, an empty clip
     * replaces the file, which is the same thing to the next reader.
     */
    private fun clearClipboard(context: Context) {
        val clipboard = context.getSystemService(ClipboardManager::class.java) ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            clipboard.clearPrimaryClip()
        } else {
            clipboard.setPrimaryClip(ClipData.newPlainText("", ""))
        }
    }
}

/**
 * What the Keys page will do on Save, kept apart from the widgets so it
 * can be tested on the JVM: which of [Keys.NAMES] the phone holds now,
 * what was typed or pasted since, and which were cleared. The rules a
 * test pins: an empty field changes nothing (the dialog opens with every
 * field empty and stored values are never shown back, so empty cannot
 * mean remove); a typed value replaces; Clear removes a stored key and
 * forgets what was typed; a pasted `.env` follows the same words, a
 * blank `NAME=` clearing that key as it did when the whole file was the
 * page; the last action on a name wins; and [changes] comes in the
 * engine's order with a blank value meaning remove -- the shape
 * [Keys.putAll] takes, so Save is one call. What lost: reading the nine
 * fields at Save and treating an empty one as remove, which would have
 * wiped every key on the first Save of a dialog opened to tick the
 * kiosk box.
 */
class KeyEdits(present: Collection<String>) {
    private val present: Set<String> = present.filter { it in Keys.NAMES }.toSet()
    private val typed = LinkedHashMap<String, String>()
    private val cleared = LinkedHashSet<String>()

    /**
     * What the field for [name] holds now. A non-blank value replaces the
     * stored one on Save (and undoes a [clear]); blank means "as it was"
     * and undoes only an earlier [type]. A name the engine does not read
     * is ignored.
     */
    fun type(name: String, value: String) {
        if (name !in Keys.NAMES) return
        val text = value.trim()
        if (text.isEmpty()) {
            typed.remove(name)
        } else {
            typed[name] = text
            cleared.remove(name)
        }
    }

    /** Take [name] out on Save: a stored key is removed and whatever was typed is forgotten. */
    fun clear(name: String) {
        if (name !in Keys.NAMES) return
        typed.remove(name)
        if (name in present) cleared.add(name)
    }

    /**
     * Apply a parsed `.env` ([Keys.parseEnvText]): a value types it, a
     * blank one clears it, a name the engine does not read is ignored.
     * Returns the known names it touched, in the file's order, so the
     * dialog can say which (names, never values).
     */
    fun fill(values: Map<String, String>): List<String> {
        val known = Keys.known(values)
        for ((name, value) in known) {
            if (value.isBlank()) clear(name) else type(name, value)
        }
        return known.keys.toList()
    }

    /** What the field for [name] should show: the value typed or pasted, or null when none is. */
    fun typedValue(name: String): String? = typed[name]

    /** Whether the phone will hold [name] after Save. */
    fun willHave(name: String): Boolean = name in typed || (name in present && name !in cleared)

    /** Whether the engine could think after Save: [Keys.canThink]'s rule over what will be held. */
    fun canThink(): Boolean = Keys.AI_KEYS.any { willHave(it) }

    /**
     * What Save stores, in [Keys.NAMES] order: the value to put, or ""
     * to remove -- [Keys.putAll]'s shape. Empty when nothing changed.
     */
    fun changes(): Map<String, String> {
        val out = LinkedHashMap<String, String>()
        for (name in Keys.NAMES) {
            val value = typed[name]
            if (value != null) {
                out[name] = value
            } else if (name in cleared) {
                out[name] = ""
            }
        }
        return out
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
