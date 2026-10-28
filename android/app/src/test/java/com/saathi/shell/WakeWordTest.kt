/**
 * The wake word's state machine on the JVM, with a fake spotter: what is
 * pressed, sent and released, in what order, and when the mic is wanted.
 * The spotter itself (sherpa-onnx) was measured on the laptop
 * (DECISIONS.md, 2026-10-29); here it is the part around it that runs
 * on every frame of her day.
 */
package com.saathi.shell

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeWordTest {
    private val frameBytes = Protocol.Audio.CHUNK_BYTES
    private val frameMs = frameBytes * 1000 / (Protocol.Audio.SAMPLE_RATE * 2)

    /** A frame whose every sample is [level] (so its rms is [level]); [tag] in the first byte pair when non-zero. */
    private fun frame(level: Int, tag: Int = 0): ByteArray {
        val out = ByteArray(frameBytes)
        for (i in 0 until frameBytes / 2) {
            val sample = if (i == 0 && tag != 0) tag else level
            out[2 * i] = (sample and 0xff).toByte()
            out[2 * i + 1] = (sample shr 8).toByte()
        }
        return out
    }

    private class FakeSpotter : Spotter {
        var hearOn: Int = -1
        var fed = 0
        var resets = 0

        override fun accept(pcm: ByteArray): Boolean = fed++ == hearOn

        override fun reset() {
            resets++
        }
    }

    private class Recorder : WakeWord.Actions {
        val events = mutableListOf<String>()
        var preroll: ByteArray? = null

        override fun micWanted(on: Boolean) {
            events += "mic:$on"
        }

        override fun press() {
            events += "press"
        }

        override fun send(preroll: ByteArray) {
            events += "send"
            this.preroll = preroll
        }

        override fun release() {
            events += "release"
        }
    }

    @Test
    fun theMicIsWantedOnlyWhileAllowed() {
        val actions = Recorder()
        val wake = WakeWord(FakeSpotter(), actions)
        wake.setAllowed(true)
        wake.setAllowed(false)
        assertEquals(listOf("mic:true", "mic:false"), actions.events)
    }

    @Test
    fun nothingIsPressedWhileNotAllowedEvenIfTheWordIsHeard() {
        val spotter = FakeSpotter().apply { hearOn = 0 }
        val actions = Recorder()
        val wake = WakeWord(spotter, actions)
        repeat(5) { wake.onFrame(frame(500)) }
        assertEquals(emptyList<String>(), actions.events)
        assertEquals(0, spotter.fed) // not even run: no work while she is not to be heard
    }

    @Test
    fun theWordPressesThenSendsTheNameAndHerRequestThenReleasesWhenSheStops() {
        val spotter = FakeSpotter().apply { hearOn = 9 }
        val actions = Recorder()
        val wake = WakeWord(spotter, actions)
        wake.setAllowed(true)
        actions.events.clear()

        repeat(9) { wake.onFrame(frame(0)) } // a quiet room
        wake.onFrame(frame(800, tag = 1)) // "Melody" ends here
        assertEquals(listOf("press"), actions.events)

        // The press lead: the engine needs the press first.
        val lead = WakeWord.PRESS_LEAD_MS / frameMs
        repeat(lead - 1) { wake.onFrame(frame(800)) }
        assertEquals(listOf("press"), actions.events)
        wake.onFrame(frame(800, tag = 2))
        assertEquals(listOf("press", "send"), actions.events)

        // The preroll holds the name and everything after it, ending with the latest frame.
        val preroll = actions.preroll!!
        assertTrue(preroll.size <= WakeWord.PREROLL_BYTES)
        assertEquals(0, preroll.size % frameBytes)
        val frames = preroll.toList().chunked(frameBytes).map { it.toByteArray() }
        assertArrayEquals(frame(800, tag = 2), frames.last())
        assertTrue(frames.any { it.contentEquals(frame(800, tag = 1)) })

        // She speaks, then stops: released after the silence, and the mic goes back to spotting.
        repeat(10) { wake.onFrame(frame(800)) }
        assertEquals(listOf("press", "send"), actions.events)
        repeat(WakeEndpointer.SILENCE_END_MS / frameMs) { wake.onFrame(frame(0)) }
        assertEquals(listOf("press", "send", "release", "mic:true"), actions.events)
        assertEquals(1, spotter.resets)
    }

    @Test
    fun aSilentRoomIsNotDecodedButTheOnsetOfSoundIs() {
        val spotter = FakeSpotter()
        val fed = mutableListOf<Int>()
        val counting = object : Spotter {
            override fun accept(pcm: ByteArray): Boolean {
                fed += rms(pcm)
                return spotter.accept(pcm)
            }

            override fun reset() = spotter.reset()
        }
        val wake = WakeWord(counting, Recorder())
        wake.setAllowed(true)
        wake.onFrame(frame(500))
        repeat(WakeWord.HANGOVER_FRAMES + 50) { wake.onFrame(frame(0)) }
        // The sound, then its hangover, then nothing for the rest of the silence.
        assertEquals(listOf(500) + List(WakeWord.HANGOVER_FRAMES) { 0 }, fed)
        assertEquals(1, spotter.resets)

        fed.clear()
        wake.onFrame(frame(300))
        // The last silent frame (the onset) goes in ahead of the sound.
        assertEquals(listOf(0, 300), fed)
    }

    @Test
    fun theTurnKeepsTheMicWhenTheEngineStopsListeningMidTurn() {
        // The engine says listen:false as soon as the press makes it busy.
        val actions = Recorder()
        val wake = WakeWord(FakeSpotter().apply { hearOn = 0 }, actions)
        wake.setAllowed(true)
        wake.onFrame(frame(800))
        wake.setAllowed(false)
        assertEquals(listOf("mic:true", "press", "mic:true"), actions.events)

        repeat(WakeEndpointer.NO_SPEECH_MS / frameMs) { wake.onFrame(frame(0)) }
        // Released, and now the mic is let go: the engine is busy answering.
        assertEquals(listOf("mic:true", "press", "mic:true", "send", "release", "mic:false"), actions.events)
    }

    @Test
    fun onlyTheNameIsSentWhenSheSaysNothingMore() {
        val actions = Recorder()
        val wake = WakeWord(FakeSpotter().apply { hearOn = 0 }, actions)
        wake.setAllowed(true)
        wake.onFrame(frame(800))
        repeat(WakeEndpointer.NO_SPEECH_MS / frameMs - 1) { wake.onFrame(frame(0)) }
        assertEquals(listOf("mic:true", "press", "send"), actions.events)
        wake.onFrame(frame(0))
        assertEquals(listOf("mic:true", "press", "send", "release", "mic:true"), actions.events)
    }

    @Test
    fun aTurnThatNeverStopsIsEndedAtTheCap() {
        val actions = Recorder()
        val wake = WakeWord(FakeSpotter().apply { hearOn = 0 }, actions)
        wake.setAllowed(true)
        wake.onFrame(frame(800))
        repeat(WakeEndpointer.MAX_MS / frameMs - 1) { wake.onFrame(frame(800)) }
        assertTrue("release" !in actions.events)
        wake.onFrame(frame(800))
        assertTrue("release" in actions.events)
    }

    @Test
    fun theEndpointerIgnoresTheNamesTailAndScalesWithTheRoom() {
        // Speech only inside the grace period: still "nothing said".
        val quiet = WakeEndpointer(noiseFloor = 0)
        repeat(WakeEndpointer.GRACE_MS / frameMs) { assertTrue(!quiet.push(frame(800))) }
        repeat((WakeEndpointer.NO_SPEECH_MS - WakeEndpointer.GRACE_MS) / frameMs - 1) {
            assertTrue(!quiet.push(frame(0)))
        }
        assertTrue(quiet.push(frame(0)))

        // A noisy room (floor 100): 200 is not speech, 400 is.
        val noisy = WakeEndpointer(noiseFloor = 100)
        repeat(WakeEndpointer.GRACE_MS / frameMs + 1) { noisy.push(frame(200)) }
        repeat(WakeEndpointer.SILENCE_END_MS / frameMs + 1) { assertTrue(!noisy.push(frame(200))) }
        noisy.push(frame(400))
        repeat(WakeEndpointer.SILENCE_END_MS / frameMs - 1) { assertTrue(!noisy.push(frame(200))) }
        assertTrue(noisy.push(frame(200)))
    }

    @Test
    fun theRingKeepsTheNewestBytesInOrder() {
        val ring = PcmRing(4)
        ring.add(byteArrayOf(1, 2, 3))
        assertArrayEquals(byteArrayOf(1, 2, 3), ring.snapshot())
        ring.add(byteArrayOf(4, 5, 6))
        assertArrayEquals(byteArrayOf(3, 4, 5, 6), ring.snapshot())
        ring.clear()
        assertArrayEquals(byteArrayOf(), ring.snapshot())
    }

    @Test
    fun rmsOfAConstantFrameIsItsLevel() {
        assertEquals(800, rms(frame(800)))
        assertEquals(800, rms(frame(-800)))
        assertEquals(0, rms(byteArrayOf()))
    }
}
