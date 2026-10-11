/**
 * The private-range rule applied to what a page loads, not only to where
 * it navigates.
 *
 * Why this file exists: the network security config permits cleartext
 * everywhere, because it cannot say "private ranges only" (it has no
 * CIDR; `res/xml/network_security_config.xml` says what lost there), so
 * the rule lives in code. `EngineAddress.normalise()` applies it to the
 * address someone types and the two WebViewClients' main-frame rules to
 * where a page may go -- but an `<img>`, a `<script>` or an `<iframe>`
 * at `http://<public host>` inside either page is a sub-resource, which
 * those rules never see, and it loaded over cleartext (found in review,
 * 2026-10-08). `shouldInterceptRequest` sees every request a page makes;
 * both clients hand each one here, and a cleartext request to a host off
 * the home network gets an empty 403 instead of a fetch. The engine's
 * own pages (loopback or the LAN) and youtube.com (https) are untouched.
 *
 * What lost: a false `cleartextTrafficPermitted` in the base config with
 * the engine's hosts listed (every household a rebuild, the original
 * reason); a `shouldInterceptRequest` that returns a response for every
 * request (it would have to fetch everything itself, cookies and all);
 * and a rule in each client (the same ten lines twice, one drifting).
 * Pure where it can be: the decision is `EngineAddress.isCleartextOffLan`,
 * tested on the JVM; this file is the WebView plumbing around it.
 */
package com.saathi.shell

import android.util.Log
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import java.io.ByteArrayInputStream

object CleartextGuard {
    private const val TAG = "SaathiCleartext"

    /**
     * An empty 403 for a cleartext request to a host off the home
     * network, or null to let the WebView fetch [request] as usual.
     * Called on a WebView worker thread, as `shouldInterceptRequest`
     * is; nothing here touches a view.
     */
    fun intercept(request: WebResourceRequest): WebResourceResponse? {
        val url = request.url
        if (!EngineAddress.isCleartextOffLan(url.scheme, url.host)) return null
        Log.w(TAG, "refusing cleartext off the home network: $url")
        return WebResourceResponse(
            "text/plain",
            "utf-8",
            403,
            "Forbidden",
            emptyMap(),
            ByteArrayInputStream(ByteArray(0)),
        )
    }
}
