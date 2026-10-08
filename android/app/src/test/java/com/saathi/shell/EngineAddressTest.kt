/**
 * The private-range rule the network security config could not express,
 * pinned here: what the setup dialog stores, and what it refuses.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EngineAddressTest {
    @Test
    fun normalisesWhatPeopleType() {
        assertEquals("http://192.168.1.20:8765", EngineAddress.normalise("192.168.1.20"))
        assertEquals("http://192.168.1.20:8765", EngineAddress.normalise(" 192.168.1.20:8765/ "))
        assertEquals("http://192.168.1.20:9000", EngineAddress.normalise("http://192.168.1.20:9000/"))
        assertEquals("http://saathi.local:8765", EngineAddress.normalise("saathi.local"))
        assertEquals("https://10.0.0.5:8765", EngineAddress.normalise("HTTPS://10.0.0.5"))
        assertEquals("http://10.0.2.2:8765", EngineAddress.normalise("http://10.0.2.2:8765/index.html?x=1"))
        assertEquals("http://localhost:8765", EngineAddress.normalise("localhost"))
    }

    @Test
    fun refusesWhatIsNotTheHomeNetwork() {
        assertNull(EngineAddress.normalise(""))
        assertNull(EngineAddress.normalise("   "))
        assertNull(EngineAddress.normalise("example.com"))
        assertNull(EngineAddress.normalise("http://8.8.8.8:8765"))
        assertNull(EngineAddress.normalise("ftp://192.168.1.20"))
        assertNull(EngineAddress.normalise("http://user:pw@192.168.1.20:8765"))
        assertNull(EngineAddress.normalise("http://192.168.1.999:8765"))
        assertNull(EngineAddress.normalise("not a url at all"))
    }

    @Test
    fun privateRanges() {
        for (host in listOf("10.0.0.1", "10.255.255.254", "172.16.0.1", "172.31.9.9", "192.168.0.1",
            "169.254.1.1", "127.0.0.1", "localhost", "pi.local", "LOCALHOST", "::1", "[fe80::1]", "fd12::1")) {
            assertTrue(host, EngineAddress.isPrivateLan(host))
        }
        for (host in listOf("172.15.0.1", "172.32.0.1", "11.0.0.1", "192.169.0.1", "1.1.1.1",
            "google.com", "2001:db8::1", "", "10.0.0", "10.0.0.0.1")) {
            assertFalse(host, EngineAddress.isPrivateLan(host))
        }
    }

    @Test
    fun theFaceStaysOnTheEngine() {
        val base = "http://192.168.1.20:8765"
        for (url in listOf(base, "$base/", "$base/index.html", "$base/?demo=media", "$base#x",
            "HTTP://192.168.1.20:8765/static/js/main.js")) {
            assertTrue(url, EngineAddress.isOn(url, base))
        }
        for (url in listOf("https://www.youtube.com/watch?v=x", "http://192.168.1.20:87650/",
            "http://192.168.1.200:8765/", "http://192.168.1.20/", "intent://x#Intent;end", "about:blank", "")) {
            assertFalse(url, EngineAddress.isOn(url, base))
        }
        assertTrue(EngineAddress.isOn("$base/", "$base/"))
        assertFalse(EngineAddress.isOn("$base/", ""))
    }

    @Test
    fun socketUrls() {
        assertEquals("ws://192.168.1.20:8765/ws", EngineAddress.socketUrl("http://192.168.1.20:8765", Protocol.WS_PATH))
        assertEquals("ws://192.168.1.20:8765/audio", EngineAddress.socketUrl("http://192.168.1.20:8765/", Protocol.AUDIO_PATH))
        assertEquals("wss://pi.local:8765/ws", EngineAddress.socketUrl("https://pi.local:8765", "/ws"))
    }
}
