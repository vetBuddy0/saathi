/**
 * The watch-page rules, pinned: what is a wall, what is YouTube at all,
 * what user agent the pane wears, and what the scripts it injects say.
 * The WebView that applies them is not tested here (it needs a device);
 * the scripts' behaviour on a page is not either (there is no JS engine
 * on the JVM test path), so this pins their shape -- the bridge name,
 * the quoted id, the level, the deadlines -- and the pane's rules.
 */
package com.saathi.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WatchPageTest {
    private val webViewUa =
        "Mozilla/5.0 (Linux; Android 13; Pixel 6 Build/TQ3A.230805.001; wv) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.54 Mobile Safari/537.36"

    @Test
    fun theDesktopUserAgentKeepsThePlatformsChromeVersionAndNothingElse() {
        val ua = WatchPage.desktopUserAgent(webViewUa)
        assertEquals(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
                "Chrome/124.0.6367.54 Safari/537.36",
            ua,
        )
        assertFalse(ua.contains("wv"))
        assertFalse(ua.contains("Mobile"))
        assertFalse(ua.contains("Android"))
    }

    @Test
    fun theFallbackUserAgentIsWornWhenThereIsNoChromeVersionToKeep() {
        assertEquals(WatchPage.FALLBACK_USER_AGENT, WatchPage.desktopUserAgent(null))
        assertEquals(WatchPage.FALLBACK_USER_AGENT, WatchPage.desktopUserAgent(""))
        assertEquals(WatchPage.FALLBACK_USER_AGENT, WatchPage.desktopUserAgent("Mozilla/5.0 (Linux; Android 13)"))
        assertTrue(WatchPage.FALLBACK_USER_AGENT.contains("Windows NT"))
        assertTrue(WatchPage.FALLBACK_USER_AGENT.contains("Chrome/"))
    }

    @Test
    fun hostAndPathAreReadFromWhatTheWebViewReports() {
        assertEquals("www.youtube.com", WatchPage.host("https://www.youtube.com/watch?v=abc"))
        assertEquals("accounts.google.com", WatchPage.host("https://user@ACCOUNTS.GOOGLE.COM:443/signin?x=1#f"))
        assertEquals("[::1]", WatchPage.host("http://[::1]:8765/ws"))
        assertEquals("youtube.com", WatchPage.host("https://youtube.com."))
        assertNull(WatchPage.host(null))
        assertNull(WatchPage.host("about:blank"))
        assertNull(WatchPage.host("not a url"))
        assertNull(WatchPage.host("https://"))
        assertEquals("/sorry/index", WatchPage.path("https://www.google.com/sorry/index?continue=https://x/#y"))
        assertEquals("", WatchPage.path("https://www.youtube.com"))
        assertEquals("", WatchPage.path("https://www.youtube.com?v=1"))
        assertEquals("", WatchPage.path(null))
        assertEquals("https", WatchPage.scheme("HTTPS://x"))
        assertEquals("intent", WatchPage.scheme("intent://x#Intent;end"))
        assertEquals("about", WatchPage.scheme("about:blank"))
        assertNull(WatchPage.scheme("/relative"))
        assertNull(WatchPage.scheme("1abc://x"))
    }

    @Test
    fun aWallIsASignInConsentOrSorryPage() {
        assertTrue(WatchPage.isWall("https://accounts.google.com/ServiceLogin?service=youtube"))
        assertTrue(WatchPage.isWall("https://accounts.google.com/v3/signin/identifier?flowName=x"))
        assertTrue(WatchPage.isWall("https://ACCOUNTS.GOOGLE.COM/"))
        assertTrue(WatchPage.isWall("https://consent.youtube.com/m?continue=https://www.youtube.com/watch"))
        assertTrue(WatchPage.isWall("https://consent.google.com/ml?continue=x"))
        assertTrue(WatchPage.isWall("https://www.google.com/sorry/index?continue=https://www.youtube.com/"))
        assertTrue(WatchPage.isWall("https://www.youtube.com/sorry/index"))
    }

    @Test
    fun aWatchPageIsNotAWall() {
        assertFalse(WatchPage.isWall("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        assertFalse(WatchPage.isWall("https://www.youtube.com/watch?v=sorry"))
        assertFalse(WatchPage.isWall("https://www.youtube.com/watch?continue=https://accounts.google.com/"))
        assertFalse(WatchPage.isWall("https://www.youtube.com/accounts.google.com"))
        assertFalse(WatchPage.isWall("https://accounts.google.com.example.net/"))
        assertFalse(WatchPage.isWall("https://notsorry.example/"))
        assertFalse(WatchPage.isWall("about:blank"))
        assertFalse(WatchPage.isWall(""))
        assertFalse(WatchPage.isWall(null))
    }

    @Test
    fun thePaneOpensHttpsYouTubeAndNothingElse() {
        assertTrue(WatchPage.isWatchUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        assertTrue(WatchPage.isWatchUrl("https://youtube.com/watch?v=x"))
        assertTrue(WatchPage.isWatchUrl("https://m.youtube.com/watch?v=x"))
        assertTrue(WatchPage.isWatchUrl("https://youtu.be/x"))
        assertFalse(WatchPage.isWatchUrl("http://www.youtube.com/watch?v=x"))
        assertFalse(WatchPage.isWatchUrl("https://www.youtube.com.example.net/watch?v=x"))
        assertFalse(WatchPage.isWatchUrl("https://example.net/watch?v=x"))
        assertFalse(WatchPage.isWatchUrl("https://accounts.google.com/"))
        assertFalse(WatchPage.isWatchUrl("about:blank"))
        assertFalse(WatchPage.isWatchUrl(""))
        assertFalse(WatchPage.isWatchUrl(null))
    }

    @Test
    fun youTubeHostsAndHttpSchemesAreNamed() {
        assertTrue(WatchPage.isYouTube("http://www.youtube.com/"))
        assertTrue(WatchPage.isYouTube("https://consent.youtube.com/"))
        assertFalse(WatchPage.isYouTube("https://www.google.com/"))
        assertTrue(WatchPage.isHttp("http://x/"))
        assertTrue(WatchPage.isHttp("HTTPS://x/"))
        assertFalse(WatchPage.isHttp("intent://x#Intent;end"))
        assertFalse(WatchPage.isHttp("vnd.youtube:abc"))
        assertFalse(WatchPage.isHttp("about:blank"))
        assertFalse(WatchPage.isHttp(null))
    }

    @Test
    fun theInstallScriptNamesTheBridgeTheVideoAndTheLevel() {
        val script = WatchPage.installScript("dQw4w9WgXcQ", 70)
        assertTrue(script.contains("window.SaathiBridge"))
        assertTrue(script.contains("bridge.onEnded(id)"))
        assertTrue(script.contains("bridge.onError(id, String(code))"))
        assertTrue(script.contains("var id = \"dQw4w9WgXcQ\";"))
        assertTrue(script.contains("v.volume = 70 / 100;"))
        assertTrue(script.contains("document.querySelector(\"video\")"))
        assertTrue(script.contains("addEventListener(\"ended\""))
        assertTrue(script.contains("addEventListener(\"error\""))
        assertTrue(script.contains("setTimeout(find, 2000);"))
        assertTrue(script.contains("tries > 15"))
        assertTrue(script.contains("}, 30000);"))
        assertTrue(script.contains("}, 2000);"))
        assertTrue(script.contains("\"no_video\""))
        assertTrue(script.contains("\"no_start\""))
    }

    @Test
    fun theInstallScriptTellsTheBridgeWhenTheVideoIsFound() {
        val script = WatchPage.installScript("abc", 70)
        // Once, from attach, after the element's level and listeners are
        // set: the pane answers with the level it holds by then.
        assertEquals(1, Regex("""bridge\.onVideo\(id\)""").findAll(script).count())
        assertTrue(script.indexOf("v.volume = 70 / 100;") < script.indexOf("bridge.onVideo(id)"))
        assertTrue(script.indexOf("addEventListener(\"error\"") < script.indexOf("bridge.onVideo(id)"))
    }

    @Test
    fun theInstallScriptClampsTheLevelAndQuotesTheId() {
        assertTrue(WatchPage.installScript("x", 150).contains("v.volume = 100 / 100;"))
        assertTrue(WatchPage.installScript("x", -1).contains("v.volume = 0 / 100;"))
        val hostile = WatchPage.installScript("a\"b</script>\n", 50)
        assertTrue(hostile.contains("var id = \"a\\\"b<"))
        assertFalse(hostile.contains("</script>"))
        assertFalse(hostile.contains("var id = \"a\"b"))
    }

    @Test
    fun thePollCoversThirtySeconds() {
        assertEquals(30_000L, WatchPage.POLL_MS * WatchPage.POLL_TRIES)
        assertEquals(30_000L, WatchPage.START_TIMEOUT_MS)
        assertEquals(2000L, WatchPage.ENDED_GRACE_MS)
        assertEquals(70, WatchPage.DEFAULT_VOLUME)
    }

    @Test
    fun pauseResumeAndVolumeTouchOnlyTheVideoElement() {
        val pause = WatchPage.pauseScript()
        assertTrue(pause.contains("window.__saathiHold = true"))
        assertTrue(pause.contains("v.pause()"))
        val resume = WatchPage.resumeScript()
        assertTrue(resume.contains("window.__saathiHold = false"))
        assertTrue(resume.contains("v.play()"))
        assertTrue(resume.contains("p.catch(function () {})"))
        assertTrue(WatchPage.volumeScript(35).contains("v.volume = 35 / 100;"))
        assertTrue(WatchPage.volumeScript(999).contains("v.volume = 100 / 100;"))
        assertTrue(WatchPage.volumeScript(-5).contains("v.volume = 0 / 100;"))
        for (script in listOf(pause, resume, WatchPage.volumeScript(35), WatchPage.installScript("x", 1))) {
            assertFalse(script.contains("click("))
            assertFalse(script.contains("skip"))
            assertFalse(script.contains("googlevideo"))
            assertTrue(script.contains("document.querySelector(\"video\")"))
        }
    }

    @Test
    fun theWallHostsAndSignInAreWhatTheBrief() {
        assertEquals(setOf("accounts.google.com", "consent.youtube.com", "consent.google.com"), WatchPage.WALL_HOSTS)
        assertEquals("/sorry/", WatchPage.SORRY_PATH)
        assertTrue(WatchPage.isWall(WatchPage.SIGN_IN_URL))
        assertEquals("accounts.google.com", WatchPage.host(WatchPage.SIGN_IN_URL))
        assertEquals("SaathiBridge", WatchPage.BRIDGE)
    }
}
