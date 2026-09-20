package com.androiduse.client

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
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
    private var server: ServerSocket? = null
    private val pool = Executors.newFixedThreadPool(4)
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

    private fun handle(socket: Socket) {
        socket.use { s ->
            s.soTimeout = 20_000
            val reader = BufferedReader(InputStreamReader(s.getInputStream()))
            val request = reader.readLine() ?: return
            val parts = request.split(" ")
            if (parts.size < 2) return respond(s.getOutputStream(), 400, err("Malformed request"))
            val method = parts[0]
            // Exact paths only: with prefix matching, "/screenshot" is also a
            // prefix match for "/screen" and never reaches its own handler.
            val path = parts[1].substringBefore('?').trimEnd('/').ifEmpty { "/" }

            var contentLength = 0
            var auth: String? = null
            while (true) {
                val line = reader.readLine() ?: break
                if (line.isEmpty()) break
                val lower = line.lowercase()
                if (lower.startsWith("content-length:")) {
                    contentLength = line.substringAfter(":").trim().toIntOrNull() ?: 0
                } else if (lower.startsWith("authorization:")) {
                    auth = line.substringAfter(":").trim().removePrefix("Bearer ").trim()
                }
            }
            val bodyText = if (contentLength > 0) {
                CharArray(contentLength).let { buf ->
                    var read = 0
                    while (read < contentLength) {
                        val n = reader.read(buf, read, contentLength - read)
                        if (n < 0) break
                        read += n
                    }
                    String(buf, 0, read)
                }
            } else ""

            val out = s.getOutputStream()

            // /ping is the only route that works without a token, so a client
            // can tell "wrong address" apart from "wrong credentials".
            if (path == "/ping") {
                return respond(out, 200, JSONObject().put("ok", true).put("app", "android-use"))
            }
            if (!Security.tokenMatches(context, auth)) {
                return respond(out, 401, err("Bad or missing token."))
            }
            if (!Security.isGranted(context) && path != "/status") {
                return respond(
                    out, 403,
                    err("Control is not currently granted. Open Android Use on the phone and grant access.")
                )
            }

            val body = if (bodyText.isBlank()) JSONObject() else runCatching {
                JSONObject(bodyText)
            }.getOrDefault(JSONObject())

            try {
                route(method, path, body, out)
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
        403 -> "Forbidden"; 404 -> "Not Found"; else -> "Error"
    }

    // --------------------------------------------------------------- routes

    private fun service(): ControlAccessibilityService? = ControlAccessibilityService.instance

    private fun route(method: String, path: String, body: JSONObject, out: OutputStream) {
        val svc = service()
        if (svc == null && path != "/status" && path != "/repair") {
            return respond(
                out, 503,
                err("The accessibility service is not running. Enable Android Use in Settings > Accessibility.")
            )
        }

        when {
            path == "/status" -> respond(
                out, 200,
                JSONObject()
                    .put("service_running", svc != null)
                    .put("granted", Security.isGranted(context))
                    .put("grant_remaining_ms", Security.grantRemainingMs(context))
                    .put("model", android.os.Build.MODEL)
                    .put("manufacturer", android.os.Build.MANUFACTURER)
                    .put("android", android.os.Build.VERSION.RELEASE)
                    .put("sdk", android.os.Build.VERSION.SDK_INT)
            )

            path == "/screen" -> respond(out, 200, svc!!.readScreen())

            path == "/screenshot" -> {
                val bytes = svc!!.screenshot()
                if (bytes == null) respond(
                    out, 500,
                    err("Screenshot failed (system error ${Screenshots.lastError}). " +
                        "Screenshots are rate limited; try again in a moment.")
                )
                else respondBinary(out, bytes, "image/jpeg")
            }

            path == "/tap" -> {
                val index = body.optInt("index", -1)
                val ok = if (index >= 0) svc!!.clickIndex(index)
                else svc!!.tap(
                    body.optDouble("x", 0.0).toFloat(),
                    body.optDouble("y", 0.0).toFloat()
                )
                respond(out, 200, JSONObject().put("ok", ok))
            }

            path == "/text" -> {
                val text = body.optString("text", "")
                val index = body.optInt("index", -1)
                // ACTION_SET_TEXT carries any script or emoji, unlike adb input.
                val ok = if (index >= 0) svc!!.setTextAt(index, text) else false
                respond(out, 200, JSONObject().put("ok", ok))
            }

            path == "/swipe" -> respond(
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

            path == "/longpress" -> respond(
                out, 200,
                JSONObject().put(
                    "ok",
                    svc!!.tap(
                        body.optDouble("x").toFloat(), body.optDouble("y").toFloat(), 700L
                    )
                )
            )

            path == "/pinch" -> respond(
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

            path == "/key" -> respond(
                out, 200,
                JSONObject().put("ok", svc!!.globalAction(body.optString("key", "")))
            )

            path == "/apps" -> {
                val pm = context.packageManager
                val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                val list = JSONArray()
                pm.queryIntentActivities(intent, 0).forEach { info ->
                    val pkg = info.activityInfo.packageName
                    list.put(
                        JSONObject()
                            .put("package", pkg)
                            .put("label", info.loadLabel(pm).toString())
                            .put("allowed", Security.isAllowed(context, pkg))
                            .put("sensitive", Security.looksSensitive(pkg))
                    )
                }
                respond(out, 200, JSONObject().put("apps", list))
            }

            path == "/launch" -> {
                val pkg = body.optString("package", "")
                if (!Security.isAllowed(context, pkg)) {
                    return respond(
                        out, 403,
                        err("'$pkg' is not on this phone's allowlist. The owner must allow it in the Android Use app.")
                    )
                }
                val launch = context.packageManager.getLaunchIntentForPackage(pkg)
                    ?: return respond(out, 404, err("No launcher activity for $pkg."))
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(launch)
                respond(out, 200, JSONObject().put("ok", true))
            }

            path == "/settings" -> {
                val action = body.optString("action", "")
                val intent = Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                if (body.optString("package").isNotBlank()) {
                    intent.data = Uri.parse("package:${body.optString("package")}")
                }
                context.startActivity(intent)
                respond(out, 200, JSONObject().put("ok", true))
            }

            path == "/secure_setting" -> {
                // Works only because WRITE_SECURE_SETTINGS was granted over adb
                // during setup; without it this returns a clear failure.
                val key = body.optString("key", "")
                val value = body.optString("value", "")
                val ok = runCatching {
                    Settings.Global.putString(context.contentResolver, key, value)
                }.getOrDefault(false)
                respond(
                    out, if (ok) 200 else 403,
                    if (ok) JSONObject().put("ok", true)
                    else err("Could not write '$key'. WRITE_SECURE_SETTINGS was not granted.")
                )
            }

            path == "/repair" -> {
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

            path == "/revoke" -> {
                Security.revoke(context)
                respond(out, 200, JSONObject().put("ok", true))
            }

            else -> respond(out, 404, err("Unknown route: $path"))
        }
    }
}
