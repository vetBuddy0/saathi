/**
 * The reconnect schedule, which is the one pure piece of the link. The
 * rest touches AudioRecord and a socket and is exercised on a device.
 * This test loads the class but never constructs it, so it runs in a
 * local unit test where the platform classes are stubs.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Test

class OkHttpAudioLinkTest {
    @Test
    fun backoffDoublesFromOneSecond() {
        assertEquals(1000L, OkHttpAudioLink.backoffMs(0))
        assertEquals(2000L, OkHttpAudioLink.backoffMs(1))
        assertEquals(4000L, OkHttpAudioLink.backoffMs(2))
        assertEquals(8000L, OkHttpAudioLink.backoffMs(3))
    }

    @Test
    fun backoffIsCappedAtFifteenSeconds() {
        assertEquals(15000L, OkHttpAudioLink.backoffMs(4))
        assertEquals(15000L, OkHttpAudioLink.backoffMs(10))
        assertEquals(15000L, OkHttpAudioLink.backoffMs(1000))
        assertEquals(15000L, OkHttpAudioLink.backoffMs(Int.MAX_VALUE))
    }

    @Test
    fun aNegativeAttemptIsTheFirst() {
        assertEquals(1000L, OkHttpAudioLink.backoffMs(-3))
    }
}
