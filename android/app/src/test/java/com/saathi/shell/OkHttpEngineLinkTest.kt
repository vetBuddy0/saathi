/**
 * Which `/ws` frames reach an [EngineLink.Listener], and which are the
 * face page's and stop at the link: the delivery rule, without a socket.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpEngineLinkTest {
    private fun deliver(text: String, listener: EngineLink.Listener): Boolean =
        OkHttpEngineLink.deliver(text, listener)

    private class Recording : EngineLink.Listener {
        val states = mutableListOf<String>()
        val media = mutableListOf<MediaMessage>()
        val phone = mutableListOf<PhoneMessage>()
        var connections = 0
        var disconnections = 0

        override fun onState(state: String) {
            states += state
        }

        override fun onMedia(message: MediaMessage) {
            media += message
        }

        override fun onPhone(message: PhoneMessage) {
            phone += message
        }

        override fun onConnected() {
            connections += 1
        }

        override fun onDisconnected() {
            disconnections += 1
        }
    }

    @Test
    fun stateFramesReachTheListener() {
        val listener = Recording()
        assertTrue(deliver("""{"type":"state","state":"listening"}""", listener))
        assertTrue(deliver("""{"type":"state","state":"idle"}""", listener))
        assertEquals(listOf("listening", "idle"), listener.states)
    }

    @Test
    fun everyMediaFrameReachesTheListenerWhateverItsTarget() {
        val listener = Recording()
        val browser = """{"type":"media","action":"play","video_id":"abc","target":"browser",
            "watch_url":"https://www.youtube.com/watch?v=abc","volume":60}"""
        assertTrue(deliver(browser, listener))
        assertTrue(deliver("""{"type":"media","action":"play","video_id":"x","target":"embed"}""", listener))
        assertTrue(deliver("""{"type":"media","action":"volume","level":20}""", listener))
        assertTrue(deliver("""{"type":"media","action":"results","items":[]}""", listener))
        assertEquals(listOf("play", "play", "volume", "results"), listener.media.map { it.action })
        assertTrue(listener.media[0].isBrowserPlay)
        assertFalse(listener.media[1].isBrowserPlay)
        assertEquals(20, listener.media[2].level)
    }

    @Test
    fun theFacePagesFramesAndUnknownsStopHere() {
        val listener = Recording()
        assertFalse(deliver("""{"type":"card","card":{"id":"c1"}}""", listener))
        assertFalse(deliver("""{"type":"caption","who":"her","text":"hi"}""", listener))
        assertFalse(deliver("""{"type":"settings","languages":["en"]}""", listener))
        assertFalse(deliver("""{"type":"preference_result","ok":true}""", listener))
        assertFalse(deliver("""{"type":"state"}""", listener))
        assertFalse(deliver("not json", listener))
        assertFalse(deliver("[1,2]", listener))
        assertTrue(listener.states.isEmpty())
        assertTrue(listener.media.isEmpty())
        assertEquals(0, listener.connections)
        assertEquals(0, listener.disconnections)
    }

    @Test
    fun aPhoneDialFrameReachesTheListenerWithItsNumber() {
        val listener = Recording()
        assertTrue(deliver("""{"type":"phone","action":"dial","number":"+6589614304"}""", listener))
        assertEquals(listOf(PhoneMessage("dial", "+6589614304")), listener.phone)
    }

    @Test
    fun thePhoneStatusFrameIsWhatTheEngineReads() {
        val frame = org.json.JSONObject(Protocol.phoneStatus(true))
        assertEquals("phone_status", frame.getString("type"))
        assertTrue(frame.getBoolean("can_call"))
        assertFalse(org.json.JSONObject(Protocol.phoneStatus(false)).getBoolean("can_call"))
    }
}
