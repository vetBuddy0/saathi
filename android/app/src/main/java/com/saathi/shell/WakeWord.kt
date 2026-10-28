/**
 * "Melody": the tablet's wake word, heard on the tablet.
 *
 * Why it exists: the owner asked for a spoken way in (2026-10-29), and the
 * laptop's (`audio/wake.py`: Silero VAD + faster-whisper tiny.en) cannot
 * run on the phone -- the embedded Python has neither onnxruntime nor
 * ctranslate2 wheels. So the shell spots the word itself, natively.
 *
 * How: sherpa-onnx's keyword spotter, an open-vocabulary 3.3M-parameter
 * zipformer (Apache-2.0, ~5 MB int8 in `assets/kws/`), told the word as
 * text. Measured on the laptop with the same model and settings
 * (DECISIONS.md, 2026-10-29): "Melody ..." caught in 36 of 36 synthetic
 * clips across six voices (35 of 36 at the tablet's quiet level), no
 * wake in 3.4 minutes of ordinary talk or in nine of her real turns, at
 * ~4% of one laptop core. The one near-miss it takes is "malady"; the
 * engine's second check (the cloud transcript must contain the name,
 * `mentions_name`) throws that turn away silently, as the laptop does.
 *
 * The privacy line holds: frames go to the spotter on the device, and
 * nothing is sent until the word is heard. What is sent then is the
 * 2.5 s before it (the name itself, which the second check needs) and
 * her request, until an energy endpointer hears her stop.
 *
 * Whether to listen at all is the engine's call (`{"type":"wake"}`):
 * her setting on, the engine idle or asleep, no call on screen. While a
 * turn this word started is running the mic stays with it whatever the
 * engine says, so the endpointer can end it.
 *
 * Lost: Picovoice Porcupine (better accuracy, but a new vendor with an
 * access key and a licence -- CLAUDE.md asks first); Android's
 * SpeechRecognizer in a loop (not made for always-on: beeps, restarts,
 * and on this tablet no on-device recogniser to rely on); sending audio
 * to the cloud to find the name (breaks "nothing leaves the device until
 * she asks").
 */
package com.saathi.shell

import android.content.res.AssetManager
import android.util.Log
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import kotlin.math.sqrt

/** Hears the word in a stream of 16 kHz PCM16 mono frames. Called on one thread. */
interface Spotter {
    /** Feed one frame; true when the word ended in it (or just before). */
    fun accept(pcm: ByteArray): Boolean

    /** Forget what was heard so far: a fresh start after a turn. */
    fun reset()
}

class SherpaSpotter private constructor(private val kws: KeywordSpotter) : Spotter {
    private var stream: OnlineStream = kws.createStream()

    override fun accept(pcm: ByteArray): Boolean {
        stream.acceptWaveform(toFloats(pcm), Protocol.Audio.SAMPLE_RATE)
        var heard = false
        while (kws.isReady(stream)) {
            kws.decode(stream)
            if (kws.getResult(stream).keyword.isNotBlank()) {
                heard = true
                kws.reset(stream)
            }
        }
        return heard
    }

    override fun reset() {
        stream.release()
        stream = kws.createStream()
    }

    companion object {
        private const val TAG = "SaathiWake"
        private const val DIR = "kws"

        /**
         * The spotter, or null where it cannot run (no arm64 native
         * library -- the x86_64 emulator -- or a missing asset): the
         * button still works, and the log says why there is no word.
         */
        fun load(assets: AssetManager): SherpaSpotter? =
            try {
                val config = KeywordSpotterConfig(
                    featConfig = FeatureConfig(sampleRate = Protocol.Audio.SAMPLE_RATE, featureDim = 80),
                    modelConfig = OnlineModelConfig(
                        transducer = OnlineTransducerModelConfig(
                            encoder = "$DIR/encoder.int8.onnx",
                            decoder = "$DIR/decoder.int8.onnx",
                            joiner = "$DIR/joiner.int8.onnx",
                        ),
                        tokens = "$DIR/tokens.txt",
                        modelType = "zipformer2",
                        numThreads = 1,
                    ),
                    keywordsFile = "$DIR/keywords.txt",
                    // The file's own ":0.5 #0.4" wins; these match it.
                    keywordsScore = 0.5f,
                    keywordsThreshold = 0.4f,
                )
                SherpaSpotter(KeywordSpotter(assets, config))
            } catch (e: Throwable) {
                // UnsatisfiedLinkError is an Error, not an Exception.
                Log.w(TAG, "no wake word on this device: ${e.javaClass.simpleName}: ${e.message}")
                null
            }

        fun toFloats(pcm: ByteArray): FloatArray =
            FloatArray(pcm.size / 2) { i ->
                val sample = (pcm[2 * i].toInt() and 0xff) or (pcm[2 * i + 1].toInt() shl 8)
                sample.toShort() / 32768f
            }
    }
}

/** The last [capacity] bytes of PCM, oldest first. Thread-safe. */
class PcmRing(private val capacity: Int) {
    private val buffer = ByteArray(capacity)
    private var start = 0
    private var size = 0

    @Synchronized
    fun add(frame: ByteArray) {
        for (byte in frame) {
            buffer[(start + size) % capacity] = byte
            if (size < capacity) size++ else start = (start + 1) % capacity
        }
    }

    @Synchronized
    fun snapshot(): ByteArray = ByteArray(size) { buffer[(start + it) % capacity] }

    @Synchronized
    fun clear() {
        start = 0
        size = 0
    }
}

/** Root-mean-square of a PCM16 frame, in sample units (0..32767). */
fun rms(pcm: ByteArray): Int {
    val n = pcm.size / 2
    if (n == 0) return 0
    var sum = 0.0
    for (i in 0 until n) {
        val s = ((pcm[2 * i].toInt() and 0xff) or (pcm[2 * i + 1].toInt() shl 8)).toShort().toDouble()
        sum += s * s
    }
    return sqrt(sum / n).toInt()
}

/**
 * When a turn the word started is over, with nothing but loudness: the
 * platform's noise suppressor (VOICE_COMMUNICATION) gates this tablet's
 * silence to near zero (her kept turns: 10th-percentile frame rms 0), so
 * a floor-relative threshold is enough here, where the engine's Silero
 * endpointer cannot run.
 *
 * Speech in the first [GRACE_MS] does not count -- that is the tail of
 * the name the spotter just heard. Then: [SILENCE_END_MS] of quiet after
 * she has spoken ends it; nothing at all for [NO_SPEECH_MS] ends it (only
 * her name -- the engine answers that one); [MAX_MS] ends it regardless.
 */
class WakeEndpointer(noiseFloor: Int) {
    private val threshold = maxOf(MIN_SPEECH_RMS, noiseFloor * FLOOR_FACTOR)
    private var elapsedMs = 0
    private var spoke = false
    private var quietMs = 0

    /** One frame of the turn, in order; true once the turn should end. */
    fun push(frame: ByteArray): Boolean {
        val ms = frame.size * 1000 / (Protocol.Audio.SAMPLE_RATE * 2)
        elapsedMs += ms
        val loud = rms(frame) >= threshold
        if (elapsedMs > GRACE_MS && loud) {
            spoke = true
            quietMs = 0
        } else if (spoke && !loud) {
            quietMs += ms
        }
        return (spoke && quietMs >= SILENCE_END_MS) ||
            (!spoke && elapsedMs >= NO_SPEECH_MS) ||
            elapsedMs >= MAX_MS
    }

    companion object {
        const val GRACE_MS = 300
        const val SILENCE_END_MS = 900
        const val NO_SPEECH_MS = 5000
        const val MAX_MS = 20000
        const val MIN_SPEECH_RMS = 40
        const val FLOOR_FACTOR = 3
    }
}

/**
 * The wake word's state machine, fed every mic frame on the mic thread.
 * Pure (no Android), so it is tested on the JVM with a fake [Spotter].
 */
class WakeWord(private val spotter: Spotter, private val actions: Actions) {
    interface Actions {
        /** Whether the mic should stay open between holds (frames to [onFrame]). */
        fun micWanted(on: Boolean)

        /** The word was heard: press for her, by name. */
        fun press()

        /** Start the turn's audio, [preroll] first. */
        fun send(preroll: ByteArray)

        /** She has finished: stop sending, then release. */
        fun release()
    }

    private enum class Phase { SPOTTING, PRESSED, SENDING }

    @Volatile
    private var allowed = false

    @Volatile
    private var phase = Phase.SPOTTING
    private val ring = PcmRing(PREROLL_BYTES)
    private val levels = ArrayDeque<Int>()
    private var endpointer: WakeEndpointer? = null
    private var sincePressMs = 0

    /** Silent frames in a row, and the last one (fed first when sound returns). */
    private var silentFrames = 0
    private var lastSilent: ByteArray? = null

    /** The engine's `{"type":"wake","listen":...}`; on the main thread. */
    fun setAllowed(on: Boolean) {
        allowed = on
        actions.micWanted(on || phase != Phase.SPOTTING)
    }

    /** One frame from the mic (whether or not it is being sent). */
    fun onFrame(frame: ByteArray) {
        ring.add(frame)
        when (phase) {
            Phase.SPOTTING -> {
                val level = rms(frame)
                levels.addLast(level)
                if (levels.size > LEVEL_FRAMES) levels.removeFirst()
                if (allowed && hearsIn(frame, level)) {
                    phase = Phase.PRESSED
                    sincePressMs = 0
                    endpointer = WakeEndpointer(noiseFloor())
                    actions.press()
                }
            }
            Phase.PRESSED -> {
                val done = endpointer!!.push(frame)
                sincePressMs += frame.size * 1000 / (Protocol.Audio.SAMPLE_RATE * 2)
                if (sincePressMs >= PRESS_LEAD_MS) {
                    // The engine drops /audio frames until the press has
                    // started her turn, so the turn's audio waits a moment
                    // and arrives as one preroll: the name and what came
                    // after it, nothing lost to the gap.
                    phase = Phase.SENDING
                    actions.send(ring.snapshot())
                    if (done) finish()
                }
            }
            Phase.SENDING -> if (endpointer!!.push(frame)) finish()
        }
    }

    /**
     * The spotter costs ~30% of one tablet core (measured 2026-10-29), and
     * the platform's noise suppressor gates a quiet room to zeros: frames
     * that silent are not decoded, past a [HANGOVER_FRAMES] tail. When
     * sound comes back the last silent frame goes in first, so the soft
     * onset of the "M" is not lost; after a long silence the spotter
     * starts fresh rather than joining two sounds seconds apart.
     */
    private fun hearsIn(frame: ByteArray, level: Int): Boolean {
        if (level <= SILENT_RMS) {
            silentFrames++
            if (silentFrames <= HANGOVER_FRAMES) return spotter.accept(frame)
            if (silentFrames == HANGOVER_FRAMES + 1) spotter.reset()
            lastSilent = frame
            return false
        }
        val skipped = silentFrames > HANGOVER_FRAMES
        silentFrames = 0
        val onset = lastSilent
        lastSilent = null
        if (skipped && onset != null && spotter.accept(onset)) return true
        return spotter.accept(frame)
    }

    private fun finish() {
        phase = Phase.SPOTTING
        endpointer = null
        ring.clear()
        levels.clear()
        silentFrames = 0
        lastSilent = null
        spotter.reset()
        actions.release()
        actions.micWanted(allowed)
    }

    /**
     * The room's level before the name: the last [NAME_FRAMES] are left
     * out, or a name said as the mic opened would be its own floor and
     * her request would read as quiet (found by WakeWordTest).
     */
    private fun noiseFloor(): Int {
        val before = levels.toList().dropLast(NAME_FRAMES)
        if (before.isEmpty()) return 0
        val sorted = before.sorted()
        return sorted[sorted.size / 5]
    }

    companion object {
        /** 2.5 s: the name (up to ~1 s) plus the spotter's lag and the press lead. */
        const val PREROLL_BYTES = Protocol.Audio.SAMPLE_RATE * 2 * 5 / 2
        const val PRESS_LEAD_MS = 200
        const val LEVEL_FRAMES = 40

        /** ~1 s: the name itself, up to the frame the spotter fired on. */
        const val NAME_FRAMES = 10

        /** At or below this a frame is the suppressor's silence (her turns: 10th percentile 0). */
        const val SILENT_RMS = 4

        /** ~1 s of silence still decoded: the end of a word, the gap inside one. */
        const val HANGOVER_FRAMES = 10
    }
}
