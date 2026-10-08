/**
 * The one gate an engine address passes through.
 *
 * Why this file exists: the engine is plain http on the home network at an
 * address someone types once, and Android's network security config has
 * no way to say "cleartext to private ranges only" (its domain entries
 * are host names, not CIDR blocks -- see res/xml/network_security_config.xml
 * for what lost there). So the rule lives here instead: nothing that is
 * not RFC 1918, link-local, loopback or a `.local` name is ever stored,
 * and the shell loads no cleartext URL it did not get from [normalise].
 * Pure Kotlin, no Android imports, so it runs on the JVM under JUnit; the
 * SharedPreferences half is `Settings.kt`.
 *
 * [DEFAULT] is a placeholder, not a device name in the sense CLAUDE.md
 * forbids: it is the text the setup dialog shows pre-filled so the person
 * setting up sees the shape of what to type, and it is replaced the first
 * time they save. Nothing is detected from it and nothing depends on it
 * being right.
 */
package com.saathi.shell

import java.net.URI
import java.net.URISyntaxException

object EngineAddress {
    const val DEFAULT = "http://192.168.1.10:8765"
    const val DEFAULT_PORT = 8765

    /**
     * `scheme://host:port` for what the setup dialog was given, or null if
     * it is not an address this shell will talk to. A missing scheme is
     * `http`, a missing port is [DEFAULT_PORT], a path or query is dropped.
     */
    fun normalise(raw: String): String? {
        var text = raw.trim().trimEnd('/')
        if (text.isEmpty()) return null
        if (!text.contains("://")) text = "http://$text"
        val uri = try {
            URI(text)
        } catch (e: URISyntaxException) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        if (uri.userInfo != null) return null
        val host = uri.host ?: return null
        if (!isPrivateLan(host)) return null
        val port = if (uri.port == -1) DEFAULT_PORT else uri.port
        return "$scheme://$host:$port"
    }

    /** RFC 1918, link-local, loopback, ULA/link-local IPv6, `localhost` and `.local` names. */
    fun isPrivateLan(host: String): Boolean {
        val h = host.trim().trim('[', ']').lowercase()
        if (h.isEmpty()) return false
        if (h == "localhost" || h.endsWith(".localhost") || h.endsWith(".local")) return true
        if (h.contains(':')) {
            return h == "::1" || h.startsWith("fe80:") || h.startsWith("fc") || h.startsWith("fd")
        }
        val parts = h.split('.')
        if (parts.size != 4) return false
        val octets = parts.map { it.toIntOrNull() ?: return false }
        if (octets.any { it !in 0..255 }) return false
        val (a, b) = octets
        return a == 10 ||
            a == 127 ||
            (a == 192 && b == 168) ||
            (a == 172 && b in 16..31) ||
            (a == 169 && b == 254)
    }

    /**
     * Whether [url] is a page of the engine at [baseUrl] (a normalised
     * base): the base itself, or anything under it. The face WebView
     * refuses every main-frame navigation that is not, so a link inside
     * the face page -- the embed's "watch on YouTube" -- cannot replace
     * the face. A literal prefix match on what [normalise] stored, since
     * the page was loaded from exactly that string.
     */
    fun isOn(url: String, baseUrl: String): Boolean {
        val base = baseUrl.trimEnd('/')
        if (base.isEmpty()) return false
        if (url.equals(base, ignoreCase = true)) return true
        return url.startsWith("$base/", ignoreCase = true) ||
            url.startsWith("$base?", ignoreCase = true) ||
            url.startsWith("$base#", ignoreCase = true)
    }

    /** `ws://host:port/path` (or `wss`) for a normalised base and a path like [Protocol.WS_PATH]. */
    fun socketUrl(baseUrl: String, path: String): String {
        val base = baseUrl.trimEnd('/')
        val socket = when {
            base.startsWith("https://", ignoreCase = true) -> "wss://" + base.substring("https://".length)
            base.startsWith("http://", ignoreCase = true) -> "ws://" + base.substring("http://".length)
            else -> base
        }
        return socket + path
    }
}
