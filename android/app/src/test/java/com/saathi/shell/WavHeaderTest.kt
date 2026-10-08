/**
 * The header reader against the files the engine's backends actually
 * write (Python's `wave`: 44 bytes, PCM16) and the shapes they could
 * write tomorrow (a LIST chunk, an odd-sized chunk, a streaming size
 * field, WAVE_FORMAT_EXTENSIBLE), plus what it must refuse.
 */
package com.saathi.shell

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class WavHeaderTest {
    @Test
    fun readsWhatPythonsWaveModuleWrites() {
        val pcm = ByteArray(16000 * 2) // one second, 16 kHz mono
        val header = WavHeader.parse(wav(16000, 1, pcm))
        assertNotNull(header)
        header!!
        assertEquals(16000, header.sampleRate)
        assertEquals(1, header.channels)
        assertEquals(16, header.bitsPerSample)
        assertEquals(44, header.dataOffset)
        assertEquals(pcm.size, header.dataLength)
        assertEquals(2, header.frameBytes)
        assertEquals(16000, header.frames)
        assertEquals(1000L, header.durationMs)
    }

    @Test
    fun stereoAtAnotherRate() {
        val pcm = ByteArray(22050 * 4 / 2) // half a second, 22050 Hz stereo
        val header = WavHeader.parse(wav(22050, 2, pcm))!!
        assertEquals(22050, header.sampleRate)
        assertEquals(2, header.channels)
        assertEquals(4, header.frameBytes)
        assertEquals(11025, header.frames)
        assertEquals(500L, header.durationMs)
    }

    @Test
    fun durationRoundsDownToTheMillisecond() {
        val header = WavHeader.parse(wav(24000, 1, ByteArray(12001 * 2)))!!
        assertEquals(12001, header.frames)
        assertEquals(500L, header.durationMs)
    }

    @Test
    fun skipsAListChunkBeforeData() {
        val list = chunk("LIST", "INFOISFT\u0007\u0000\u0000\u0000Lavf58\u0000\u0000".toByteArray(Charsets.ISO_8859_1))
        val pcm = ByteArray(400)
        val header = WavHeader.parse(wav(16000, 1, pcm, extraChunks = listOf(list)))!!
        assertEquals(44 + list.size, header.dataOffset)
        assertEquals(400, header.dataLength)
    }

    @Test
    fun padsAnOddSizedChunk() {
        val odd = chunk("junk", ByteArray(7)) // 8 + 7 + 1 pad
        assertEquals(16, odd.size)
        val header = WavHeader.parse(wav(16000, 1, ByteArray(200), extraChunks = listOf(odd)))!!
        assertEquals(44 + 16, header.dataOffset)
        assertEquals(200, header.dataLength)
    }

    @Test
    fun clampsAStreamingSizeFieldToTheBytesPresent() {
        // 321 data bytes and no pad byte: not a whole number of frames either.
        val bytes = wav(16000, 1, ByteArray(321)).copyOf(44 + 321)
        ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).putInt(40, -1) // 0xFFFFFFFF
        val header = WavHeader.parse(bytes)!!
        assertEquals(44, header.dataOffset)
        assertEquals(320, header.dataLength)
        assertEquals(160, header.frames)
    }

    @Test
    fun truncatedDataIsClampedToo() {
        val bytes = wav(16000, 1, ByteArray(1000)).copyOf(44 + 100)
        val header = WavHeader.parse(bytes)!!
        assertEquals(100, header.dataLength)
    }

    @Test
    fun anEmptyDataChunkIsAHeaderWithNoFrames() {
        val header = WavHeader.parse(wav(16000, 1, ByteArray(0)))!!
        assertEquals(0, header.dataLength)
        assertEquals(0, header.frames)
        assertEquals(0L, header.durationMs)
    }

    @Test
    fun acceptsExtensiblePcm() {
        val header = WavHeader.parse(wav(48000, 2, ByteArray(960), extensible = true))!!
        assertEquals(48000, header.sampleRate)
        assertEquals(2, header.channels)
        assertEquals(44 + 24, header.dataOffset) // the fmt chunk is 40 bytes, not 16
        assertEquals(960, header.dataLength)
    }

    @Test
    fun refusesWhatTheSpeakerCannotPlay() {
        assertNull("empty", WavHeader.parse(ByteArray(0)))
        assertNull("short", WavHeader.parse(ByteArray(20)))
        assertNull("not riff", WavHeader.parse("RIFX....WAVE".toByteArray() + ByteArray(40)))
        assertNull("8-bit", WavHeader.parse(wav(16000, 1, ByteArray(100), bits = 8)))
        assertNull("float", WavHeader.parse(wav(16000, 1, ByteArray(100), format = 3)))
        assertNull("three channels", WavHeader.parse(wav(16000, 3, ByteArray(120))))
        assertNull("zero rate", WavHeader.parse(wav(0, 1, ByteArray(100))))
        assertNull("no data chunk", WavHeader.parse(wav(16000, 1, ByteArray(100)).copyOf(36)))
        assertNull("fmt too short", WavHeader.parse(wav(16000, 1, ByteArray(100)).copyOf(30)))
        assertNull("extensible with float subformat", WavHeader.parse(wav(16000, 1, ByteArray(100), extensible = true, format = 3)))
    }

    @Test
    fun aDataChunkBeforeFmtStillReads() {
        val out = ByteArrayOutputStream()
        val data = chunk("data", ByteArray(100))
        val fmt = fmtChunk(16000, 1, 16, 1, extensible = false)
        val body = data + fmt
        out.write("RIFF".toByteArray())
        out.write(le32(4 + body.size))
        out.write("WAVE".toByteArray())
        out.write(body)
        val header = WavHeader.parse(out.toByteArray())!!
        assertEquals(20, header.dataOffset)
        assertEquals(100, header.dataLength)
        assertEquals(16000, header.sampleRate)
    }

    // -- builders, the way `wave` and ffmpeg lay a file out ---------------

    private fun wav(
        rate: Int,
        channels: Int,
        pcm: ByteArray,
        bits: Int = 16,
        format: Int = 1,
        extensible: Boolean = false,
        extraChunks: List<ByteArray> = emptyList(),
    ): ByteArray {
        val body = ByteArrayOutputStream()
        body.write(fmtChunk(rate, channels, bits, format, extensible))
        extraChunks.forEach { body.write(it) }
        body.write(chunk("data", pcm))
        val out = ByteArrayOutputStream()
        out.write("RIFF".toByteArray())
        out.write(le32(4 + body.size()))
        out.write("WAVE".toByteArray())
        out.write(body.toByteArray())
        return out.toByteArray()
    }

    private fun fmtChunk(rate: Int, channels: Int, bits: Int, format: Int, extensible: Boolean): ByteArray {
        val blockAlign = channels * bits / 8
        val fmt = ByteArrayOutputStream()
        fmt.write(le16(if (extensible) 0xFFFE else format))
        fmt.write(le16(channels))
        fmt.write(le32(rate))
        fmt.write(le32(rate * blockAlign))
        fmt.write(le16(blockAlign))
        fmt.write(le16(bits))
        if (extensible) {
            fmt.write(le16(22)) // cbSize
            fmt.write(le16(bits)) // valid bits
            fmt.write(le32(if (channels == 2) 3 else 4)) // channel mask
            fmt.write(le16(format)) // SubFormat GUID: format code first
            fmt.write(byteArrayOf(0, 0, 0, 0, 0x10, 0, 0x80.toByte(), 0, 0, 0xAA.toByte(), 0, 0x38, 0x9B.toByte(), 0x71))
        }
        return chunk("fmt ", fmt.toByteArray())
    }

    private fun chunk(id: String, payload: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        out.write(id.toByteArray())
        out.write(le32(payload.size))
        out.write(payload)
        if (payload.size % 2 == 1) out.write(0)
        return out.toByteArray()
    }

    private fun le16(v: Int): ByteArray = byteArrayOf((v and 0xFF).toByte(), ((v shr 8) and 0xFF).toByte())

    private fun le32(v: Int): ByteArray = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()
}
