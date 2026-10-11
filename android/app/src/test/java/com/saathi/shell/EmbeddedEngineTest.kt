/**
 * The address the engine in the app answers at, pinned: loopback on the
 * engine's own default port, in the exact form the shell's address rule
 * stores, so the face WebView and both links accept it without a special
 * case. A drift here is a phone that thinks and a face that never loads.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class EmbeddedEngineTest {
    @Test
    fun theEngineIsOnLoopbackAtTheEnginesDefaultPort() {
        assertEquals("http://127.0.0.1:8765", EmbeddedEngine.URL)
        assertEquals("127.0.0.1", EmbeddedEngine.HOST)
        assertEquals(EngineAddress.DEFAULT_PORT, EmbeddedEngine.PORT)
        assertEquals("saathi.android", EmbeddedEngine.MODULE)
    }

    @Test
    fun theShellAcceptsItsOwnAddressAsItIs() {
        assertEquals(EmbeddedEngine.URL, EngineAddress.normalise(EmbeddedEngine.URL))
        assertTrue(EngineAddress.isOn("http://127.0.0.1:8765/", EmbeddedEngine.URL))
        assertTrue(EngineAddress.isOn("http://127.0.0.1:8765/static/css/face.css", EmbeddedEngine.URL))
        assertEquals("ws://127.0.0.1:8765/ws", EngineAddress.socketUrl(EmbeddedEngine.URL, Protocol.WS_PATH))
        assertEquals("ws://127.0.0.1:8765/audio", EngineAddress.socketUrl(EmbeddedEngine.URL, Protocol.AUDIO_PATH))
    }
}
