/**
 * The pure pieces of the link: the reconnect schedule, and the table from
 * the engine's language keys to the locales the phone's voice speaks them
 * in. The rest touches AudioRecord, TextToSpeech and a socket and is
 * exercised on a device. This test loads the class but never constructs
 * it, so it runs in a local unit test where the platform classes are
 * stubs.
 */
package com.saathi.shell

import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
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

    // The two-argument constructor is what the locales are specified as;
    // JDK 19+ deprecates it (not Android), and the link builds the same
    // values with forLanguageTag, which this pins.
    @Suppress("DEPRECATION")
    @Test
    fun theEnginesLanguageKeysMapToTheirLocales() {
        assertEquals(Locale.US, OkHttpAudioLink.localeFor("english"))
        assertEquals(Locale.SIMPLIFIED_CHINESE, OkHttpAudioLink.localeFor("chinese"))
        assertEquals(Locale("hi", "IN"), OkHttpAudioLink.localeFor("hindi"))
        assertEquals(Locale("bn", "IN"), OkHttpAudioLink.localeFor("bengali"))
        assertEquals(Locale.forLanguageTag("hi-IN"), OkHttpAudioLink.localeFor("hindi"))
    }

    @Test
    fun aLanguageKeyThisBuildDoesNotKnowIsNotGuessed() {
        assertNull(OkHttpAudioLink.localeFor("tamil"))
        assertNull(OkHttpAudioLink.localeFor("English"))
        assertNull(OkHttpAudioLink.localeFor(""))
    }

    @Test
    fun theShellWaitsLongerForASentenceThanTheEngineDoes() {
        // audio/remote.py's DEFAULT_SYNTHESIS_TIMEOUT_SECONDS is 10: the
        // shell must never drop a sentence the engine is still waiting for.
        assertTrue(OkHttpAudioLink.SYNTHESIS_DEADLINE_MS > 10_000L)
    }
}
