/**
 * The few bytes at the front of a WAV that say how to play the rest.
 *
 * Why this file exists: every TTS sentence arrives on `/audio` as one
 * complete WAV file, and AudioTrack wants a sample rate, a channel count
 * and a pointer to the samples, not a file. Android has no WAV reader of
 * its own: MediaExtractor/MediaCodec would decode one, but through a
 * codec pipeline whose start-up is the latency the engine's sentence
 * split exists to hide, and MediaPlayer wants the bytes on disk or a URI
 * first. Forty lines of RIFF walking, pure Kotlin with no Android
 * import, so it runs under JUnit on the JVM beside Protocol and
 * EngineAddress -- the same split as `EngineAddress.kt` from
 * `Settings.kt`, for the same reason.
 *
 * What lost: trusting the engine and assuming 44 bytes of header at one
 * sample rate. The engine's backends write through Python's `wave` (44
 * bytes, PCM, 16-bit), but at rates that differ by backend (Piper 22050,
 * Kokoro 24000, the fallback 16000), and a `LIST` chunk before `data`
 * is one ffmpeg flag away. The header is read, never assumed; what it
 * says is the only thing the speaker configures from. 16-bit PCM in one
 * or two channels is all that is accepted: that is what every backend
 * here writes, and a format the engine does not produce has no test.
 */
package com.saathi.shell

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Where the PCM is in a WAV and how to play it. [dataLength] counts whole
 * frames only and never reaches past the end of the bytes it was read
 * from, whatever the chunk size field claimed.
 */
data class WavHeader(
    val sampleRate: Int,
    val channels: Int,
    val bitsPerSample: Int,
    val dataOffset: Int,
    val dataLength: Int,
) {
    /** Bytes per frame: one sample per channel. */
    val frameBytes: Int
        get() = channels * (bitsPerSample / 8)

    val frames: Int
        get() = dataLength / frameBytes

    /** How long the audio lasts; what a playback deadline is measured against. */
    val durationMs: Long
        get() = frames * 1000L / sampleRate

    companion object {
        const val FORMAT_PCM = 1
        const val FORMAT_EXTENSIBLE = 0xFFFE
        const val MAX_CHANNELS = 2
        const val BITS = 16

        private const val RIFF_HEADER_BYTES = 12
        private const val CHUNK_HEADER_BYTES = 8
        private const val FMT_MIN_BYTES = 16

        // Inside an extensible fmt chunk the real format code is the first
        // two bytes of the SubFormat GUID, at this offset from the body.
        private const val EXTENSIBLE_SUBFORMAT_OFFSET = 24
        private const val EXTENSIBLE_MIN_BYTES = EXTENSIBLE_SUBFORMAT_OFFSET + 2

        /** Null when [bytes] is not a 16-bit PCM WAV with one or two channels. */
        fun parse(bytes: ByteArray): WavHeader? {
            if (bytes.size < RIFF_HEADER_BYTES) return null
            if (!tagAt(bytes, 0, "RIFF") || !tagAt(bytes, 8, "WAVE")) return null
            val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)

            var format = -1
            var channels = 0
            var sampleRate = 0
            var bits = 0
            var dataOffset = -1
            var dataLength = 0

            var pos = RIFF_HEADER_BYTES
            while (pos + CHUNK_HEADER_BYTES <= bytes.size) {
                val size = buf.getInt(pos + 4).toLong() and 0xFFFFFFFFL
                val body = pos + CHUNK_HEADER_BYTES
                val available = (bytes.size - body).toLong()
                when {
                    tagAt(bytes, pos, "fmt ") -> {
                        if (size < FMT_MIN_BYTES || available < FMT_MIN_BYTES) return null
                        format = buf.getShort(body).toInt() and 0xFFFF
                        channels = buf.getShort(body + 2).toInt() and 0xFFFF
                        sampleRate = buf.getInt(body + 4)
                        bits = buf.getShort(body + 14).toInt() and 0xFFFF
                        if (format == FORMAT_EXTENSIBLE) {
                            if (size < EXTENSIBLE_MIN_BYTES || available < EXTENSIBLE_MIN_BYTES) return null
                            format = buf.getShort(body + EXTENSIBLE_SUBFORMAT_OFFSET).toInt() and 0xFFFF
                        }
                    }
                    tagAt(bytes, pos, "data") -> {
                        dataOffset = body
                        // A streaming writer leaves this field at 0xFFFFFFFF
                        // (or whatever it guessed); the bytes present are
                        // the truth.
                        dataLength = minOf(size, available).toInt()
                    }
                }
                // Chunks are word-aligned: an odd size has one pad byte.
                val next = body + size + (size and 1L)
                if (next > bytes.size) break
                pos = next.toInt()
            }

            if (format != FORMAT_PCM || bits != BITS) return null
            if (channels !in 1..MAX_CHANNELS || sampleRate <= 0) return null
            if (dataOffset < 0) return null
            val frameBytes = channels * (BITS / 8)
            return WavHeader(
                sampleRate = sampleRate,
                channels = channels,
                bitsPerSample = BITS,
                dataOffset = dataOffset,
                dataLength = dataLength - dataLength % frameBytes,
            )
        }

        private fun tagAt(bytes: ByteArray, offset: Int, tag: String): Boolean {
            if (offset + 4 > bytes.size) return false
            for (i in 0 until 4) {
                if (bytes[offset + i] != tag[i].code.toByte()) return false
            }
            return true
        }
    }
}
