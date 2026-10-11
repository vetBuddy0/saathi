/**
 * The target rule, without a WebView or a socket: which engine frames
 * reach the watch-page pane, which never do, and how ducking rides on
 * that. Frames are parsed from the wire shapes, as the link parses them,
 * so a test here is a test of what the engine actually sends.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MediaTargetsTest {
    private class FakePane : YouTubePane {
        override var listener: YouTubePane.Listener? = null
        val calls = mutableListOf<String>()

        override fun open(videoId: String, watchUrl: String) {
            calls += "open $videoId $watchUrl"
        }

        override fun pause() {
            calls += "pause"
        }

        override fun resume() {
            calls += "resume"
        }

        override fun stop() {
            calls += "stop"
        }

        override fun setVolume(level: Int) {
            calls += "volume $level"
        }

        override fun setFullscreen(fullscreen: Boolean) {
            calls += "fullscreen $fullscreen"
        }
    }

    private class FakeLink : EngineLink {
        override var listener: EngineLink.Listener? = null
        val sent = mutableListOf<String>()

        override fun connect(baseUrl: String) = Unit

        override fun disconnect() = Unit

        override fun sendInput(pressed: Boolean) {
            sent += if (pressed) "press" else "release"
        }

        override fun sendMediaEvent(event: String, videoId: String?, code: Any?, target: String?) {
            sent += if (target == null) "$event $videoId $code" else "$event $videoId $code $target"
        }
    }

    private val pane = FakePane()
    private val link = FakeLink()
    private val targets = MediaTargets(link, pane)

    private fun frame(text: String) {
        targets.onMedia(Protocol.parse(text) as MediaMessage)
    }

    private val browserPlay =
        """{"type":"media","action":"play","video_id":"abc","title":"T","index":1,"volume":60,
           "fullscreen":false,"target":"browser","watch_url":"https://www.youtube.com/watch?v=abc"}"""

    @Test
    fun aBrowserPlayOpensThePaneAtTheEnginesLevel() {
        frame(browserPlay)
        assertEquals(
            listOf("fullscreen false", "open abc https://www.youtube.com/watch?v=abc", "volume 60"),
            pane.calls,
        )
        assertEquals(Protocol.TARGET_BROWSER, targets.activeTarget)
        assertTrue(targets.paneActive)
        assertEquals(60, targets.baseVolume)
    }

    @Test
    fun anEmbedPlayNeverTouchesThePane() {
        frame("""{"type":"media","action":"play","video_id":"x","target":"embed","volume":80}""")
        frame("""{"type":"media","action":"play","video_id":"y"}""")
        frame("""{"type":"media","action":"pause"}""")
        frame("""{"type":"media","action":"resume"}""")
        frame("""{"type":"media","action":"volume","level":10}""")
        frame("""{"type":"media","action":"layout","mode":"fullscreen"}""")
        targets.onState("listening")
        assertTrue(pane.calls.isEmpty())
        assertEquals(Protocol.TARGET_EMBED, targets.activeTarget)
        assertFalse(targets.paneActive)
    }

    @Test
    fun controlFramesReachThePaneOnlyWhileItIsPlaying() {
        frame("""{"type":"media","action":"pause"}""")
        frame("""{"type":"media","action":"layout","mode":"fullscreen"}""")
        assertTrue(pane.calls.isEmpty())

        frame(browserPlay)
        pane.calls.clear()
        frame("""{"type":"media","action":"pause"}""")
        frame("""{"type":"media","action":"resume"}""")
        frame("""{"type":"media","action":"layout","mode":"fullscreen"}""")
        frame("""{"type":"media","action":"layout","mode":"panel"}""")
        frame("""{"type":"media","action":"results","results":[]}""")
        assertEquals(listOf("pause", "resume", "fullscreen true", "fullscreen false"), pane.calls)
    }

    @Test
    fun stateDucksThePaneAndIdleRestoresIt() {
        frame(browserPlay)
        pane.calls.clear()
        targets.onState("listening")
        targets.onState("thinking")
        targets.onState("speaking")
        targets.onState("handoff")
        targets.onState("idle")
        assertEquals(listOf("volume 12", "volume 12", "volume 12", "volume 12", "volume 60"), pane.calls)
    }

    @Test
    fun aVolumeFrameMovesTheBaseAndTheDuckedLevelFollows() {
        frame(browserPlay)
        targets.onState("speaking")
        pane.calls.clear()
        frame("""{"type":"media","action":"volume","level":100}""")
        assertEquals(listOf("volume 20"), pane.calls)
        targets.onState("idle")
        assertEquals(listOf("volume 20", "volume 100"), pane.calls)
        assertEquals(100, targets.baseVolume)
    }

    @Test
    fun aBrowserPlayDuringHerTurnStartsDucked() {
        targets.onState("speaking")
        frame(browserPlay)
        assertEquals("volume 12", pane.calls.last())
    }

    @Test
    fun stopStopsThePaneAndForgetsTheTarget() {
        frame(browserPlay)
        pane.calls.clear()
        frame("""{"type":"media","action":"stop"}""")
        assertEquals(listOf("stop"), pane.calls)
        assertNull(targets.activeTarget)
        frame("""{"type":"media","action":"pause"}""")
        targets.onState("listening")
        assertEquals(listOf("stop"), pane.calls)
    }

    @Test
    fun aStopWhileTheEmbedPlaysLeavesThePaneAlone() {
        frame("""{"type":"media","action":"play","video_id":"x","target":"embed"}""")
        frame("""{"type":"media","action":"stop"}""")
        assertTrue(pane.calls.isEmpty())
        assertNull(targets.activeTarget)
    }

    @Test
    fun thePanesOwnEndAndFailureAreReportedAndClearTheTarget() {
        frame(browserPlay)
        targets.onEnded("abc")
        assertEquals(listOf("ended abc null"), link.sent)
        assertNull(targets.activeTarget)

        frame(browserPlay)
        targets.onError("abc", Protocol.CODE_WALL)
        assertEquals(listOf("ended abc null", "error abc wall"), link.sent)
        assertNull(targets.activeTarget)
    }

    @Test
    fun aLateReportFromThePaneDoesNotForgetTheEmbed() {
        frame(browserPlay)
        frame("""{"type":"media","action":"stop"}""")
        frame("""{"type":"media","action":"play","video_id":"x","target":"embed"}""")
        targets.onEnded("abc")
        assertEquals(listOf("ended abc null"), link.sent)
        assertEquals(Protocol.TARGET_EMBED, targets.activeTarget)
    }

    @Test
    fun aPlayWithoutAWatchUrlIsOpenedAtTheCanonicalOne() {
        frame("""{"type":"media","action":"play","video_id":"abc","target":"browser"}""")
        assertEquals("open abc https://www.youtube.com/watch?v=abc", pane.calls[0])
        assertEquals("https://www.youtube.com/watch?v=abc", MediaTargets.watchUrl("abc"))
    }

    @Test
    fun aPlayWithoutAVideoIdIsNothing() {
        frame("""{"type":"media","action":"play","target":"browser","watch_url":"https://www.youtube.com/"}""")
        assertTrue(pane.calls.isEmpty())
        assertNull(targets.activeTarget)
    }

    @Test
    fun aPlayKeepsTheLastLevelAndLayoutWhenItNamesNone() {
        frame("""{"type":"media","action":"volume","level":45}""")
        frame("""{"type":"media","action":"play","video_id":"abc","target":"browser"}""")
        assertEquals(listOf("open abc https://www.youtube.com/watch?v=abc", "volume 45"), pane.calls)
    }

    @Test
    fun aSocketOpenedWithNothingPlayingTellsTheEngineThePaneIsEmpty() {
        targets.onConnected()
        assertEquals(listOf("reset null null browser"), link.sent)
        frame(browserPlay)
        targets.onConnected() // a reconnect while the pane plays says nothing
        frame("""{"type":"media","action":"stop"}""")
        frame("""{"type":"media","action":"play","video_id":"x","target":"embed"}""")
        targets.onConnected() // nor while the embed does
        assertEquals(listOf("reset null null browser"), link.sent)
        frame("""{"type":"media","action":"stop"}""")
        targets.onConnected()
        assertEquals(listOf("reset null null browser", "reset null null browser"), link.sent)
    }

    @Test
    fun thePanesOwnReportsDoNotNameATarget() {
        frame(browserPlay)
        targets.onEnded("abc")
        assertEquals(listOf("ended abc null"), link.sent) // the engine matches these by video id
    }

    @Test
    fun theDefaultLevelIsTheControllers() {
        assertEquals(WatchPage.DEFAULT_VOLUME, targets.baseVolume)
        assertEquals("idle", targets.state)
        assertEquals(WatchPage.DEFAULT_VOLUME, targets.effectiveVolume)
    }
}
