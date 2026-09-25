package com.androiduse.client

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.util.concurrent.Executors

/**
 * A very small HTTP server, written against raw sockets on purpose.
 *
 * The bridge has to be dependable on a phone that may be asleep, throttled or
 * on cellular, so it carries no third-party dependency at all - only the
 * Android framework and org.json.
 */
class HttpBridge(
    private val context: Context,
    private val port: Int = 8765
) {
    companion object {
        /**
         * Bumped whenever routes or fields are added. The server reads it from
         * /status and /screen and only asks for what this version understands,
         * so an old app gets "please update" instead of a misread request.
         */
        const val PROTOCOL = 2
        private const val MAX_HEADER = 16 * 1024
        private const val MAX_BODY = 1024 * 1024

        /** Routes that work while the accessibility service is down. */
        private val NO_SERVICE = setOf("/status", "/repair", "/request_grant", "/revoke", "/device_state")

        /** Routes that are not someone using the phone, so do not show the banner. */
        private val QUIET = setOf("/status", "/repair", "/request_grant", "/revoke")

        /** Reading or driving the app in front - refused for banking apps. */
        private val GUARDED = setOf(
            "/screen", "/screenshot", "/tap", "/doubletap", "/longpress", "/swipe",
            "/drag", "/pinch", "/scroll", "/text"
        )

        /** Gestures that could land on a message card, so it goes first. */
        private val GESTURES = setOf("/tap", "/doubletap", "/longpress", "/swipe", "/drag", "/pinch")

        /** Link schemes that can start payments or reach into apps. */
        private val BLOCKED_SCHEMES = setOf(
            "intent", "upi", "javascript", "vbscript", "file", "content", "data",
            "android-app", "chrome", "about", "smsto", "mmsto", "mms"
        )
    }

    private var server: ServerSocket? = null
    // /ask holds a thread while the owner decides, so leave room for others.
    private val pool = Executors.newFixedThreadPool(6)
    @Volatile private var running = false

    fun start() {
        if (running) return
        running = true
        Thread({
            try {
                // Bound to all interfaces so the Tailscale address works; the
                // token is what actually gates access, not the interface.
                server = ServerSocket(port)
                while (running) {
                    val client = server?.accept() ?: break
                    pool.execute { runCatching { handle(client) } }
                }
            } catch (e: Throwable) {
                if (running) Log.e("HttpBridge", "server stopped: ${e.message}")
            }
        }, "android-use-bridge").start()
    }

    fun stop() {
        running = false
        runCatching { server?.close() }
        pool.shutdownNow()
    }

    // ------------------------------------------------------------- plumbing

    private class Request(
        val method: String,
        val path: String,
        val query: Map<String, String>,
        val auth: String?,
        val body: JSONObject,
    )

    private class TooLarge(message: String) : Exception(message)

    /**
     * Parse a request as bytes. Content-Length counts bytes, and a body with
     * any non-ASCII text (a message in Telugu, an emoji) has fewer characters
     * than bytes - reading it as characters would wait for data that never comes.
     */
    private fun readRequest(input: InputStream): Request? {
        val head = ByteArrayOutputStream()
        var matched = 0  // progress through "\r\n\r\n"
        while (matched < 4) {
            val b = input.read()
            if (b < 0) return null
            head.write(b)
            if (head.size() > MAX_HEADER) throw TooLarge("Request headers too large.")
            matched = when {
                b == '\r'.code -> if (matched == 2) 3 else 1
                b == '\n'.code && (matched == 1 || matched == 3) -> matched + 1
                else -> 0
            }
        }
        val lines = head.toString("ISO-8859-1").split("\r\n")
        val parts = lines.first().split(" ")
        if (parts.size < 2) return null
        val target = parts[1]
        // Exact paths only: with prefix matching, "/screenshot" is also a
        // prefix match for "/screen" and never reaches its own handler.
        val path = target.substringBefore('?').trimEnd('/').ifEmpty { "/" }
        val query = target.substringAfter('?', "").split('&')
            .filter { it.contains('=') }
            .associate {
                it.substringBefore('=') to runCatching {
                    URLDecoder.decode(it.substringAfter('='), "UTF-8")
                }.getOrDefault("")
            }
        var length = 0
        var auth: String? = null
        for (line in lines.drop(1)) {
            val lower = line.lowercase()
            if (lower.startsWith("content-length:")) {
                length = line.substringAfter(":").trim().toIntOrNull() ?: 0
            } else if (lower.startsWith("authorization:")) {
                auth = line.substringAfter(":").trim().removePrefix("Bearer ").trim()
            }
        }
        if (length > MAX_BODY) throw TooLarge("Request body too large.")
        val bytes = ByteArray(length.coerceAtLeast(0))
        var read = 0
        while (read < bytes.size) {
            val n = input.read(bytes, read, bytes.size - read)
            if (n < 0) break
            read += n
        }
        val text = String(bytes, 0, read, Charsets.UTF_8)
        val body = if (text.isBlank()) JSONObject() else runCatching { JSONObject(text) }.getOrDefault(JSONObject())
        return Request(parts[0], path, query, auth, body)
    }

    private fun handle(socket: Socket) {
        socket.use { s ->
            s.soTimeout = 20_000
            val out = s.getOutputStream()
            val req = try {
                readRequest(BufferedInputStream(s.getInputStream())) ?: return
            } catch (e: TooLarge) {
                return respond(out, 413, err(e.message ?: "Too large."))
            }

            // /ping is the only route that works without a token, so a client
            // can tell "wrong address" apart from "wrong credentials".
            if (req.path == "/ping") {
                return respond(out, 200, JSONObject().put("ok", true).put("app", "android-use"))
            }
            if (!Security.tokenMatches(context, req.auth)) {
                return respond(out, 401, err("Bad or missing token."))
            }
            // Asking for a grant is the one thing allowed without one.
            if (!Security.isGranted(context) && req.path != "/status" && req.path != "/request_grant") {
                return respond(
                    out, 403,
                    err("Control is not currently granted. Open Android Use on the phone and grant access.")
                )
            }
            try {
                route(req, out)
            } catch (e: Throwable) {
                respond(out, 500, err(e.message ?: e.toString()))
            }
        }
    }

    private fun err(message: String) = JSONObject().put("error", message)

    private fun respond(out: OutputStream, code: Int, json: JSONObject) {
        val payload = json.toString().toByteArray(Charsets.UTF_8)
        val header = "HTTP/1.1 $code ${statusText(code)}\r\n" +
                "Content-Type: application/json; charset=utf-8\r\n" +
                "Content-Length: ${payload.size}\r\n" +
                "Connection: close\r\n\r\n"
        out.write(header.toByteArray())
        out.write(payload)
        out.flush()
    }

    private fun respondBinary(out: OutputStream, bytes: ByteArray, type: String) {
        val header = "HTTP/1.1 200 OK\r\nContent-Type: $type\r\n" +
                "Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
        out.write(header.toByteArray())
        out.write(bytes)
        out.flush()
    }

    private fun statusText(code: Int) = when (code) {
        200 -> "OK"; 400 -> "Bad Request"; 401 -> "Unauthorized"
        403 -> "Forbidden"; 404 -> "Not Found"; 413 -> "Payload Too Large"
        503 -> "Service Unavailable"; else -> "Error"
    }

    private fun log(message: String) = ActivityLog.add(context, message)

    // --------------------------------------------------------------- helpers

    /**
     * Ask the owner before a tap that sends, pays, deletes or calls.
     * Returns null to go ahead, or the refusal to send back.
     *
     * The server already makes the assistant confirm with the person it is
     * talking to. This asks the person holding the phone - who, when a
     * relative is helping from afar, is somebody else.
     */
    private fun ownerDeclines(svc: ControlAccessibilityService, label: String?): JSONObject? {
        if (label.isNullOrBlank() || !Security.confirmSensitive(context) || !Security.riskyLabel(label)) {
            return null
        }
        val yes = "Yes, go ahead"
        val answer = svc.overlays.ask("Your helper wants to tap “$label”.\n\nIs that OK?", listOf(yes, "No"), 60_000)
        if (answer == yes) {
            log("You allowed: tap “$label”")
            return null
        }
        log("You declined: tap “$label”")
        val why = if (answer == null) " (nobody answered within a minute)" else ""
        return JSONObject()
            .put("error", "The phone's owner did not allow tapping '$label'$why.")
            .put("declined", true)
    }

    private fun wake(): JSONObject {
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        if (!power.isInteractive || keyguard.isKeyguardLocked) {
            runCatching {
                context.startActivity(
                    Intent(context, WakeActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION)
                )
            }
            // Give the screen a moment to come on and a swipe lock to go away.
            for (i in 0 until 15) {
                Thread.sleep(100)
                if (power.isInteractive && !keyguard.isKeyguardLocked) break
            }
        }
        return JSONObject()
            .put("ok", true)
            .put("screen_on", power.isInteractive)
            .put("locked", keyguard.isKeyguardLocked)
            .put("secure", keyguard.isDeviceSecure)
    }

    private fun linkIntent(url: String, kind: String, pkg: String = ""): Intent {
        // ACTION_DIAL fills a number into the dialler and stops there;
        // ACTION_CALL, which would place the call, is never used.
        val intent = if (kind == "dial") Intent(Intent.ACTION_DIAL, Uri.parse(url))
        // BROWSABLE limits the link to what a web page could open anyway.
        else Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE)
        if (pkg.isNotBlank()) intent.setPackage(pkg)
        return intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    /** Which app a link would open: a package, "android" for the chooser, or "". */
    private fun resolvedPackage(intent: Intent): String {
        @Suppress("DEPRECATION")
        val info = context.packageManager.resolveActivity(intent, PackageManager.MATCH_DEFAULT_ONLY)
            ?: return ""
        val pkg = info.activityInfo?.packageName.orEmpty()
        val cls = info.activityInfo?.name.orEmpty()
        return if (cls.contains("ResolverActivity") || cls.contains("ChooserActivity")) "android" else pkg
    }

    private fun appLabel(pkg: String): String = runCatching {
        val pm = context.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    }.getOrDefault("")

    // --------------------------------------------------------------- routes

    private fun route(req: Request, out: OutputStream) {
        val path = req.path
        val body = req.body
        val svc = ControlAccessibilityService.instance
        if (svc == null && path !in NO_SERVICE) {
            return respond(
                out, 503,
                err("The accessibility service is not running. Enable Android Use in Settings > Accessibility.")
            )
        }
        if (svc != null && path !in QUIET) svc.overlays.activity()
        if (svc != null && path in GESTURES) svc.overlays.dismissMessage()
        if (svc != null && path in GUARDED) {
            val (fg, label) = svc.foreground()
            if (Security.isOffLimits(context, fg, label)) {
                return respond(
                    out, 403,
                    JSONObject()
                        .put("error", "A banking or payment app (${label.ifEmpty { fg }}) is on screen. " +
                            "Android Use does not read or operate it unless the owner allows that app.")
                        .put("sensitive", true)
                        .put("package", fg)
                )
            }
        }

        when (path) {
            "/status" -> {
                val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
                val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
                respond(
                    out, 200,
                    JSONObject()
                        .put("service_running", svc != null)
                        .put("granted", Security.isGranted(context))
                        .put("grant_remaining_ms", Security.grantRemainingMs(context))
                        .put("model", android.os.Build.MODEL)
                        .put("manufacturer", android.os.Build.MANUFACTURER)
                        .put("android", android.os.Build.VERSION.RELEASE)
                        .put("sdk", android.os.Build.VERSION.SDK_INT)
                        .put("protocol", PROTOCOL)
                        .put("app_version", BuildConfigVersion.name(context))
                        .put("screen_on", power.isInteractive)
                        .put("locked", keyguard.isKeyguardLocked)
                        .put("secure", keyguard.isDeviceSecure)
                )
            }

            "/screen" -> {
                if (req.query["idle"] == "1") svc!!.awaitIdle()
                val texts = (req.query["texts"]?.toIntOrNull() ?: 15).coerceIn(1, 300)
                respond(out, 200, svc!!.readScreen(texts))
            }

            "/screenshot" -> {
                val width = req.query["max_width"]?.toIntOrNull() ?: 800
                val bytes = svc!!.screenshot(width)
                if (bytes == null) respond(
                    out, 500,
                    err("Screenshot failed (system error ${Screenshots.lastError}). " +
                        "Screenshots are rate limited; try again in a moment.")
                )
                else respondBinary(out, bytes, "image/jpeg")
            }

            "/tap", "/doubletap" -> {
                val index = body.optInt("index", -1)
                val x = body.optDouble("x", 0.0).toFloat()
                val y = body.optDouble("y", 0.0).toFloat()
                val label = if (index >= 0) svc!!.labelAt(index) else svc!!.labelAtPoint(x, y)
                ownerDeclines(svc, label)?.let { return respond(out, 403, it) }
                val ok = when {
                    path == "/tap" && index >= 0 -> svc.clickIndex(index)
                    path == "/tap" -> svc.tap(x, y)
                    index >= 0 -> svc.centerAt(index)?.let { (cx, cy) -> svc.doubleTap(cx, cy) } ?: false
                    else -> svc.doubleTap(x, y)
                }
                if (ok) log("${if (path == "/tap") "Tapped" else "Double-tapped"} ${label?.let { "“$it”" } ?: "the screen"}")
                respond(out, 200, JSONObject().put("ok", ok))
            }

            "/longpress" -> {
                val index = body.optInt("index", -1)
                val ok = if (index >= 0) svc!!.longClickIndex(index)
                else svc!!.tap(body.optDouble("x").toFloat(), body.optDouble("y").toFloat(), 700L)
                respond(out, 200, JSONObject().put("ok", ok))
            }

            "/text" -> {
                val text = body.optString("text", "")
                val index = body.optInt("index", -1)
                // Replacing the contents was the only behaviour of the first
                // protocol, so that stays the default for servers that do not say.
                val result = if (index >= 0) {
                    svc!!.setTextAt(index, text, body.optBoolean("clear", true), body.optBoolean("submit", false))
                } else JSONObject().put("ok", false)
                if (result.optBoolean("ok")) {
                    log(if (result.optBoolean("password")) "Typed a password" else "Typed “${text.take(60)}”")
                }
                respond(out, 200, result)
            }

            "/swipe" -> respond(
                out, 200,
                JSONObject().put(
                    "ok",
                    svc!!.swipe(
                        body.optDouble("x1").toFloat(), body.optDouble("y1").toFloat(),
                        body.optDouble("x2").toFloat(), body.optDouble("y2").toFloat(),
                        body.optLong("duration_ms", 300L)
                    )
                )
            )

            "/drag" -> respond(
                out, 200,
                JSONObject().put(
                    "ok",
                    svc!!.drag(
                        body.optDouble("x1").toFloat(), body.optDouble("y1").toFloat(),
                        body.optDouble("x2").toFloat(), body.optDouble("y2").toFloat(),
                        body.optLong("duration_ms", 1500L)
                    )
                )
            )

            "/pinch" -> respond(
                out, 200,
                JSONObject().put(
                    "ok",
                    svc!!.pinch(
                        body.optDouble("cx").toFloat(), body.optDouble("cy").toFloat(),
                        body.optDouble("from_radius", 100.0).toFloat(),
                        body.optDouble("to_radius", 400.0).toFloat(),
                        body.optLong("duration_ms", 400L)
                    )
                )
            )

            "/scroll" -> respond(
                out, 200,
                JSONObject().put("ok", svc!!.scroll(body.optInt("index", -1), body.optString("direction", "down")))
            )

            "/key" -> {
                val key = body.optString("key", "")
                val ok = if (key == "wake") wake().optBoolean("screen_on") else svc!!.globalAction(key)
                respond(out, 200, JSONObject().put("ok", ok))
            }

            "/wake" -> respond(out, 200, wake())

            "/apps" -> {
                val pm = context.packageManager
                val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                val list = JSONArray()
                @Suppress("DEPRECATION")
                pm.queryIntentActivities(intent, 0).forEach { info ->
                    val pkg = info.activityInfo.packageName
                    val label = info.loadLabel(pm).toString()
                    list.put(
                        JSONObject()
                            .put("package", pkg)
                            .put("label", label)
                            .put("allowed", Security.isAllowed(context, pkg))
                            .put("sensitive", Security.looksSensitive(pkg, label))
                    )
                }
                respond(out, 200, JSONObject().put("apps", list))
            }

            "/launch" -> {
                val pkg = body.optString("package", "")
                if (!Security.isAllowed(context, pkg)) {
                    return respond(
                        out, 403,
                        err("'$pkg' is not on this phone's allowlist. The owner must allow it in the Android Use app.")
                            .put("blocked", true)
                    )
                }
                val launch = context.packageManager.getLaunchIntentForPackage(pkg)
                    ?: return respond(out, 404, err("No launcher activity for $pkg."))
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(launch)
                log("Opened ${appLabel(pkg).ifEmpty { pkg }}")
                respond(out, 200, JSONObject().put("ok", true))
            }

            "/settings" -> {
                val action = body.optString("action", "")
                // Settings pages only - not an open door to any intent at all.
                if (!action.startsWith("android.settings.") && action != "android.intent.action.POWER_USAGE_SUMMARY") {
                    return respond(out, 403, err("'$action' is not a settings page.").put("blocked", true))
                }
                val intent = Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                if (body.optString("package").isNotBlank()) {
                    intent.data = Uri.parse("package:${body.optString("package")}")
                }
                context.startActivity(intent)
                log("Opened settings (${action.substringAfterLast('.').lowercase().replace('_', ' ')})")
                respond(out, 200, JSONObject().put("ok", true))
            }

            "/resolve_url" -> {
                val pkg = resolvedPackage(linkIntent(body.optString("url"), body.optString("kind", "web")))
                respond(
                    out, 200,
                    JSONObject()
                        .put("package", pkg)
                        .put("label", if (pkg.isNotEmpty() && pkg != "android") appLabel(pkg) else "")
                        .put("allowed", pkg.isNotEmpty() && Security.isAllowed(context, pkg))
                        .put("browser", pkg in Security.browsers(context))
                )
            }

            "/open_url" -> {
                val url = body.optString("url")
                val kind = body.optString("kind", "web")
                val scheme = Uri.parse(url).scheme?.lowercase().orEmpty()
                if (scheme.isEmpty() || scheme in BLOCKED_SCHEMES) {
                    return respond(out, 403, err("'$scheme:' links are refused.").put("blocked", true))
                }
                val intent = linkIntent(url, kind, body.optString("package"))
                val target = resolvedPackage(intent)
                if (target.isEmpty()) return respond(out, 404, err("No app on this phone can open that link."))
                if (target != "android" && kind != "dial") {
                    val label = appLabel(target)
                    if (Security.isOffLimits(context, target, label)) {
                        return respond(
                            out, 403,
                            err("That link opens ${label.ifEmpty { target }}, a banking or payment app.")
                                .put("sensitive", true)
                        )
                    }
                    if (!Security.isAllowed(context, target) && target !in Security.browsers(context)) {
                        return respond(
                            out, 403,
                            err("That link opens ${label.ifEmpty { target }}, which is not on this phone's allowlist.")
                                .put("blocked", true)
                        )
                    }
                }
                context.startActivity(intent)
                log("Opened a link: ${url.take(80)}")
                respond(out, 200, JSONObject().put("ok", true).put("package", target))
            }

            "/device_state" -> respond(out, 200, DeviceControl.state(context))

            "/set_setting" -> {
                val name = body.optString("name")
                val result = DeviceControl.set(context, name, body.opt("value"))
                if (result.optBoolean("ok")) log("Changed a setting: ${result.optString("message")}")
                respond(out, 200, result)
            }

            "/message" -> {
                val text = body.optString("text").trim().take(500)
                if (text.isEmpty()) return respond(out, 400, err("No message."))
                svc!!.overlays.message(text)
                if (body.optBoolean("speak")) Speech.say(context, text)
                log("Message shown: ${text.take(80)}")
                respond(out, 200, JSONObject().put("ok", true))
            }

            "/ask" -> {
                val question = body.optString("question").trim().take(300)
                if (question.isEmpty()) return respond(out, 400, err("No question."))
                val options = body.optJSONArray("options")?.let { a ->
                    (0 until a.length()).map { a.optString(it).trim() }.filter { it.isNotEmpty() }.take(4)
                }.orEmpty().ifEmpty { listOf("Yes", "No") }
                val timeout = body.optInt("timeout_s", 60).coerceIn(5, 120)
                if (body.optBoolean("speak")) Speech.say(context, question)
                val answer = svc!!.overlays.ask(question, options, timeout * 1000L)
                log("Asked: ${question.take(60)} → ${answer ?: "no answer"}")
                respond(out, 200, JSONObject().put("answer", answer ?: JSONObject.NULL).put("timed_out", answer == null))
            }

            "/request_grant" -> respond(
                out, 200,
                GrantRequests.request(context, body.optInt("minutes", 60), body.optString("reason", ""))
            )

            "/repair" -> {
                // The whole point of this route: Android revokes a sideloaded
                // app's accessibility access, and without a cable there is no
                // other way back in. The bridge survives that revocation, and
                // WRITE_SECURE_SETTINGS (granted once over adb at setup) lets
                // the app put itself back in the enabled list.
                val pkg = context.packageName
                val component = "$pkg/$pkg.ControlAccessibilityService"
                val result = runCatching {
                    val resolver = context.contentResolver
                    val current = Settings.Secure.getString(
                        resolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
                    ).orEmpty()
                    val merged = when {
                        current.contains(component) -> current
                        current.isBlank() -> component
                        // Append: replacing the list would switch off whatever
                        // accessibility services the owner actually relies on.
                        else -> "$current:$component"
                    }
                    Settings.Secure.putString(
                        resolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, merged
                    )
                    Settings.Secure.putInt(resolver, Settings.Secure.ACCESSIBILITY_ENABLED, 1)
                    merged
                }
                if (result.isSuccess) {
                    Thread.sleep(2500)  // let the system bind the service
                    respond(
                        out, 200,
                        JSONObject()
                            .put("ok", true)
                            .put("service_running", ControlAccessibilityService.instance != null)
                            .put("enabled_services", result.getOrNull())
                    )
                } else {
                    respond(
                        out, 500,
                        err("Could not re-enable the accessibility service: " +
                            "${result.exceptionOrNull()?.message}. WRITE_SECURE_SETTINGS " +
                            "may not be granted - reconnect a cable and run setup again.")
                    )
                }
            }

            "/revoke" -> {
                Security.revoke(context)
                svc?.overlays?.removeAll()
                BridgeService.refresh(context)
                log("Help was stopped remotely")
                respond(out, 200, JSONObject().put("ok", true))
            }

            else -> respond(out, 404, err("Unknown route: $path"))
        }
    }
}

/** The app's version name, without depending on the generated BuildConfig. */
object BuildConfigVersion {
    fun name(ctx: Context): String = runCatching {
        ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: ""
    }.getOrDefault("")
}
