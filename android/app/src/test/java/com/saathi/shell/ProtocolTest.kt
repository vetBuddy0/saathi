/**
 * Pins the wire shapes in Protocol.kt to the engine's side, field by
 * field, so a drift between `screen/server.py` and this shell fails here
 * on the JVM rather than on a tablet in someone's living room.
 */
package com.saathi.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test
    fun stateFrameParses() {
        val message = Protocol.parse("""{"type":"state","state":"listening"}""")
        assertEquals(StateMessage("listening"), message)
    }

    @Test
    fun browserPlayCarriesTargetAndWatchUrl() {
        val text = """{"type":"media","action":"play","video_id":"abc123","title":"A song",
            "index":2,"volume":70,"fullscreen":false,"target":"browser",
            "watch_url":"https://www.youtube.com/watch?v=abc123"}"""
        val message = Protocol.parse(text) as MediaMessage
        assertEquals("play", message.action)
        assertEquals("abc123", message.videoId)
        assertEquals("A song", message.title)
        assertEquals(2, message.index)
        assertEquals(70, message.volume)
        assertEquals(false, message.fullscreen)
        assertEquals("browser", message.target)
        assertEquals("https://www.youtube.com/watch?v=abc123", message.watchUrl)
        assertTrue(message.isBrowserPlay)
    }

    @Test
    fun embedPlayIsNotTheShells() {
        val embed = Protocol.parse("""{"type":"media","action":"play","video_id":"x","target":"embed"}""")
        assertFalse((embed as MediaMessage).isBrowserPlay)
        val untargeted = Protocol.parse("""{"type":"media","action":"play","video_id":"x"}""")
        assertFalse((untargeted as MediaMessage).isBrowserPlay)
        assertNull(untargeted.target)
    }

    @Test
    fun volumeAndLayoutFramesParse() {
        val volume = Protocol.parse("""{"type":"media","action":"volume","level":35}""") as MediaMessage
        assertEquals(35, volume.level)
        val layout = Protocol.parse("""{"type":"media","action":"layout","mode":"fullscreen"}""") as MediaMessage
        assertEquals("fullscreen", layout.mode)
        val stop = Protocol.parse("""{"type":"media","action":"stop"}""") as MediaMessage
        assertEquals("stop", stop.action)
        assertNull(stop.videoId)
    }

    @Test
    fun aFloatVolumeIsStillAnInt() {
        val message = Protocol.parse("""{"type":"media","action":"volume","level":35.0}""") as MediaMessage
        assertEquals(35, message.level)
    }

    @Test
    fun cardPassesThroughAndNullClears() {
        val shown = Protocol.parse("""{"type":"card","card":{"id":"c1","kind":"choice"}}""") as CardMessage
        assertEquals("c1", shown.card?.getString("id"))
        val cleared = Protocol.parse("""{"type":"card","card":null}""") as CardMessage
        assertNull(cleared.card)
    }

    @Test
    fun captionAndSettingsAreSorted() {
        val caption = Protocol.parse("""{"type":"caption","who":"saathi","text":"Hello"}""") as CaptionMessage
        assertEquals("saathi", caption.who)
        assertEquals("Hello", caption.text)
        val settings = Protocol.parse("""{"type":"settings","languages":["en"]}""")
        assertTrue(settings is SettingsMessage)
    }

    @Test
    fun unknownTypeIsKeptNotDropped() {
        val message = Protocol.parse("""{"type":"preference_result","ok":true}""") as UnknownMessage
        assertEquals("preference_result", message.type)
        assertEquals(true, message.raw.getBoolean("ok"))
    }

    @Test
    fun nonMessagesParseToNull() {
        assertNull(Protocol.parse("not json"))
        assertNull(Protocol.parse("[1,2,3]"))
        assertNull(Protocol.parse("""{"state":"idle"}"""))
        assertNull(Protocol.parse("""{"type":7}"""))
        assertNull(Protocol.parse("""{"type":"state"}"""))
        assertNull(Protocol.parse("""{"type":"state","state":null}"""))
        assertNull(Protocol.parse("""{"type":"media"}"""))
        assertNull(Protocol.parse("""{"type":"caption","who":"her"}"""))
    }

    @Test
    fun inputBuilderMatchesTheServer() {
        assertEquals(mapOf("type" to "input", "event" to "press"), fields(Protocol.input(true)))
        assertEquals(mapOf("type" to "input", "event" to "release"), fields(Protocol.input(false)))
    }

    @Test
    fun cardAnswerBuilderMatchesTheServer() {
        val sent = JSONObject(Protocol.cardAnswer("c1", JSONObject().put("index", 2)))
        assertEquals("card_answer", sent.getString("type"))
        assertEquals("c1", sent.getString("id"))
        assertEquals(2, sent.getJSONObject("answer").getInt("index"))
    }

    @Test
    fun mediaEventBuilderLeavesOutWhatIsNull() {
        assertEquals(mapOf("type" to "media_event", "event" to "ended"), fields(Protocol.mediaEvent("ended")))
        assertEquals(mapOf("type" to "media_event", "event" to "reset"), fields(Protocol.mediaEvent("reset")))
        val error = JSONObject(Protocol.mediaEvent("error", "abc123", "wall"))
        assertEquals("error", error.getString("event"))
        assertEquals("abc123", error.getString("video_id"))
        assertEquals("wall", error.getString("code"))
        val numeric = JSONObject(Protocol.mediaEvent("error", "abc123", 150))
        assertEquals(150, numeric.getInt("code"))
    }

    @Test
    fun mediaEventNamesItsPlayerOnlyWhenAsked() {
        val named = JSONObject(Protocol.mediaEvent("reset", target = Protocol.TARGET_BROWSER))
        assertEquals("reset", named.getString("event"))
        assertEquals("browser", named.getString("target"))
        assertEquals(setOf("type", "event", "target"), fields(named.toString()).keys)
        assertEquals(setOf("type", "event"), fields(Protocol.mediaEvent("reset")).keys)
    }

    @Test
    fun setPreferenceBuilderMatchesTheServer() {
        val sent = JSONObject(Protocol.setPreference("language", "hi"))
        assertEquals("set_preference", sent.getString("type"))
        assertEquals("language", sent.getString("key"))
        assertEquals("hi", sent.getString("value"))
        assertTrue(JSONObject(Protocol.setPreference("k", null)).isNull("value"))
    }

    @Test
    fun audioConstantsAndHello() {
        assertEquals(3200, Protocol.Audio.CHUNK_BYTES)
        assertEquals(16000, Protocol.Audio.SAMPLE_RATE)
        val hello = JSONObject(Protocol.Audio.hello())
        assertEquals("hello", hello.getString("type"))
        assertEquals("android", hello.getString("client"))
        assertEquals(16000, hello.getInt("sample_rate"))
        assertEquals(mapOf("type" to "played", "id" to "p7"), fields(Protocol.Audio.played("p7")))
    }

    @Test
    fun audioFramesParse() {
        val play = Protocol.Audio.parse("""{"type":"play","id":"p7","format":"wav"}""")
        assertEquals(AudioMessage.Play("p7", "wav"), play)
        assertEquals(AudioMessage.Stop, Protocol.Audio.parse("""{"type":"stop"}"""))
        assertNull(Protocol.Audio.parse("""{"type":"play"}"""))
        assertNull(Protocol.Audio.parse("""{"type":"hello"}"""))
        assertNull(Protocol.Audio.parse("garbage"))
    }

    @Test
    fun synthesizeFrameParsesAndNeedsAllThreeFields() {
        val text = """{"type":"synthesize","id":"tts-3","text":"Good morning.","language":"hindi"}"""
        assertEquals(AudioMessage.Synthesize("tts-3", "Good morning.", "hindi"), Protocol.Audio.parse(text))
        assertNull(Protocol.Audio.parse("""{"type":"synthesize","text":"x","language":"hindi"}"""))
        assertNull(Protocol.Audio.parse("""{"type":"synthesize","id":"tts-3","language":"hindi"}"""))
        assertNull(Protocol.Audio.parse("""{"type":"synthesize","id":"tts-3","text":"x"}"""))
        assertNull(Protocol.Audio.parse("""{"type":"synthesize","id":"tts-3","text":"x","language":null}"""))
    }

    @Test
    fun synthesizedBuilderMatchesTheServerWithAndWithoutAnError() {
        assertEquals(mapOf("type" to "synthesized", "id" to "tts-3"), fields(Protocol.Audio.synthesized("tts-3")))
        assertEquals(
            mapOf("type" to "synthesized", "id" to "tts-3", "error" to "no voice installed for bengali"),
            fields(Protocol.Audio.synthesized("tts-3", "no voice installed for bengali")),
        )
    }

    @Test
    fun theLanguageKeysAreTheEngines() {
        assertEquals("english", Protocol.Audio.LANGUAGE_ENGLISH)
        assertEquals("chinese", Protocol.Audio.LANGUAGE_CHINESE)
        assertEquals("hindi", Protocol.Audio.LANGUAGE_HINDI)
        assertEquals("bengali", Protocol.Audio.LANGUAGE_BENGALI)
    }

    private fun fields(text: String): Map<String, Any> {
        val obj = JSONObject(text)
        return obj.keys().asSequence().associateWith { obj.get(it) }
    }
}
