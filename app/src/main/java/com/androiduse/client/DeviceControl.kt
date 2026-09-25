package com.androiduse.client

import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.location.LocationManager
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Environment
import android.os.PowerManager
import android.os.StatFs
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.roundToInt

/**
 * The phone's settings, read and changed directly rather than through the UI.
 *
 * "Why doesn't my phone ring?" is usually answered by one value - the ringer
 * is on silent - and fixing it through Settings means a dozen OEM-specific
 * screens. Where Android lets an app change something itself, this does. Where
 * it does not (Wi-Fi, mobile data, airplane mode), it opens the system panel
 * with the switch on it, so one tap finishes the job.
 */
object DeviceControl {

    private val STREAMS = mapOf(
        "media" to AudioManager.STREAM_MUSIC,
        "ring" to AudioManager.STREAM_RING,
        "alarm" to AudioManager.STREAM_ALARM,
        "notification" to AudioManager.STREAM_NOTIFICATION,
    )

    private fun audio(ctx: Context) = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private fun notifications(ctx: Context) =
        ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    fun canWriteSystem(ctx: Context): Boolean = Settings.System.canWrite(ctx)
    fun canControlDnd(ctx: Context): Boolean = notifications(ctx).isNotificationPolicyAccessGranted

    fun state(ctx: Context): JSONObject {
        val out = JSONObject()

        val battery = ctx.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        out.put(
            "battery", JSONObject()
                .put("level", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
                .put("charging", battery.isCharging)
        )

        val power = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
        val keyguard = ctx.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        out.put(
            "screen", JSONObject()
                .put("on", power.isInteractive)
                .put("locked", keyguard.isKeyguardLocked)
                .put("secure", keyguard.isDeviceSecure)
        )

        val am = audio(ctx)
        val volumes = JSONObject()
        STREAMS.forEach { (name, stream) ->
            val max = am.getStreamMaxVolume(stream).coerceAtLeast(1)
            volumes.put(name, (am.getStreamVolume(stream) * 100.0 / max).roundToInt())
        }
        val ringer = when (am.ringerMode) {
            AudioManager.RINGER_MODE_SILENT -> "silent"
            AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
            else -> "normal"
        }
        val dnd = when (notifications(ctx).currentInterruptionFilter) {
            NotificationManager.INTERRUPTION_FILTER_PRIORITY -> "priority only"
            NotificationManager.INTERRUPTION_FILTER_NONE -> "total silence"
            NotificationManager.INTERRUPTION_FILTER_ALARMS -> "alarms only"
            else -> "off"
        }
        out.put("sound", JSONObject().put("ringer", ringer).put("dnd", dnd).put("volume", volumes))

        val cr = ctx.contentResolver
        val brightness = runCatching {
            Settings.System.getInt(cr, Settings.System.SCREEN_BRIGHTNESS)
        }.getOrNull()
        val auto = runCatching {
            Settings.System.getInt(cr, Settings.System.SCREEN_BRIGHTNESS_MODE)
        }.getOrDefault(0) == Settings.System.SCREEN_BRIGHTNESS_MODE_AUTOMATIC
        val timeoutMs = runCatching {
            Settings.System.getInt(cr, Settings.System.SCREEN_OFF_TIMEOUT)
        }.getOrNull()
        val rotate = runCatching {
            Settings.System.getInt(cr, Settings.System.ACCELEROMETER_ROTATION)
        }.getOrDefault(0) == 1
        val display = JSONObject()
            .put("auto_brightness", auto)
            .put("font_scale", ctx.resources.configuration.fontScale.toDouble())
            .put("auto_rotate", rotate)
        brightness?.let { display.put("brightness", (it * 100.0 / 255).roundToInt().coerceIn(0, 100)) }
        timeoutMs?.let { display.put("screen_timeout_s", it / 1000) }
        out.put("display", display)

        out.put("network", network(ctx))

        runCatching {
            val stat = StatFs(Environment.getDataDirectory().path)
            val gb = 1024.0 * 1024.0 * 1024.0
            out.put(
                "storage", JSONObject()
                    .put("free_gb", (stat.availableBytes / gb * 10).roundToInt() / 10.0)
                    .put("total_gb", (stat.totalBytes / gb * 10).roundToInt() / 10.0)
            )
        }

        out.put("can_change", JSONArray(settable(ctx)))
        return out
    }

    private fun network(ctx: Context): JSONObject {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        var wifiUp = false
        var cellularUp = false
        var validated = false
        // The VPN that makes this phone reachable (Tailscale) is itself a
        // network, and often the "active" one. What matters for "is the
        // internet working" is the network underneath it.
        @Suppress("DEPRECATION")
        for (net in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(net) ?: continue
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            if (!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) continue
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) wifiUp = true
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) cellularUp = true
            if (caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) validated = true
        }
        val active = when {
            wifiUp -> "wifi"
            cellularUp -> "cellular"
            else -> "none"
        }
        val cr = ctx.contentResolver
        val wifi = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val location = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        return JSONObject()
            .put("active", active)
            .put("internet", validated)
            .put("wifi", wifi.isWifiEnabled)
            .put("mobile_data", runCatching { Settings.Global.getInt(cr, "mobile_data") }.getOrDefault(0) == 1)
            .put("airplane", Settings.Global.getInt(cr, Settings.Global.AIRPLANE_MODE_ON, 0) == 1)
            .put("bluetooth", runCatching { Settings.Global.getInt(cr, "bluetooth_on") }.getOrDefault(0) == 1)
            .put("location", location.isLocationEnabled)
    }

    fun settable(ctx: Context): List<String> {
        val out = mutableListOf(
            "volume_media", "volume_ring", "volume_alarm", "volume_notification", "ringer", "flashlight"
        )
        if (canControlDnd(ctx)) out.add("do_not_disturb")
        if (canWriteSystem(ctx)) out.addAll(listOf("brightness", "font_size", "screen_timeout", "auto_rotate"))
        return out
    }

    private fun ok(message: String) = JSONObject().put("ok", true).put("message", message)
    private fun fail(message: String) = JSONObject().put("ok", false).put("message", message)

    private fun opened(ctx: Context, action: String, what: String, message: String): JSONObject {
        val launched = runCatching {
            ctx.startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }.isSuccess
        if (!launched) return fail("$message Could not open the $what controls either.")
        return JSONObject().put("ok", false).put("opened", what).put("message", message)
    }

    private fun needsSystemWrite(what: String) = fail(
        "Changing $what needs 'Modify system settings' for Android Use, which the owner " +
            "has not allowed. They can allow it from the Android Use app; until then, " +
            "change it on screen via open_settings('display')."
    )

    /** Apply one setting. `value` arrives as the server normalised it. */
    fun set(ctx: Context, name: String, value: Any?): JSONObject {
        val cr = ctx.contentResolver
        val on = when (value) {
            is Boolean -> value
            is Number -> value.toInt() != 0
            else -> value?.toString()?.lowercase() in setOf("on", "true", "1", "yes")
        }
        if (name.startsWith("volume_")) {
            val stream = STREAMS[name.removePrefix("volume_")] ?: return fail("Unknown volume '$name'.")
            val percent = (value as? Number)?.toInt() ?: value.toString().toIntOrNull()
                ?: return fail("Volume needs a number from 0 to 100.")
            val am = audio(ctx)
            val index = (percent.coerceIn(0, 100) / 100.0 * am.getStreamMaxVolume(stream)).roundToInt()
            return runCatching {
                am.setStreamVolume(stream, index, AudioManager.FLAG_SHOW_UI)
                ok("${name.removePrefix("volume_").replaceFirstChar { it.uppercase() }} volume set to $percent%.")
            }.getOrElse {
                // Ring and notification volume are frozen while Do Not Disturb is on.
                fail("Android would not change that volume (${it.message}). Do Not Disturb may be on.")
            }
        }
        when (name) {
            "ringer" -> {
                val mode = when (value.toString().lowercase()) {
                    "silent" -> AudioManager.RINGER_MODE_SILENT
                    "vibrate" -> AudioManager.RINGER_MODE_VIBRATE
                    else -> AudioManager.RINGER_MODE_NORMAL
                }
                return runCatching {
                    audio(ctx).ringerMode = mode
                    ok("Ringer set to ${value.toString().lowercase()}.")
                }.getOrElse {
                    opened(
                        ctx, Settings.Panel.ACTION_VOLUME, "volume",
                        "Android would not change the ringer directly (Do Not Disturb access is " +
                            "needed for silent). Opened the volume panel instead."
                    )
                }
            }
            "do_not_disturb" -> {
                if (!canControlDnd(ctx)) {
                    return opened(
                        ctx, "android.settings.ZEN_MODE_SETTINGS", "Do Not Disturb",
                        "Android Use has not been given Do Not Disturb access, so it opened the " +
                            "Do Not Disturb settings - tap the switch there."
                    )
                }
                notifications(ctx).setInterruptionFilter(
                    if (on) NotificationManager.INTERRUPTION_FILTER_PRIORITY
                    else NotificationManager.INTERRUPTION_FILTER_ALL
                )
                return ok("Do Not Disturb turned ${if (on) "on" else "off"}.")
            }
            "flashlight" -> {
                val cameras = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
                val id = cameras.cameraIdList.firstOrNull { cid ->
                    cameras.getCameraCharacteristics(cid)
                        .get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
                } ?: return fail("This phone has no flashlight.")
                return runCatching {
                    cameras.setTorchMode(id, on)
                    ok("Flashlight turned ${if (on) "on" else "off"}.")
                }.getOrElse { fail("The flashlight is in use by another app (${it.message}).") }
            }
            "brightness" -> {
                if (!canWriteSystem(ctx)) return needsSystemWrite("brightness")
                if (value.toString().lowercase() == "auto") {
                    Settings.System.putInt(
                        cr, Settings.System.SCREEN_BRIGHTNESS_MODE,
                        Settings.System.SCREEN_BRIGHTNESS_MODE_AUTOMATIC
                    )
                    return ok("Brightness set to automatic.")
                }
                val percent = (value as? Number)?.toInt() ?: value.toString().toIntOrNull()
                    ?: return fail("Brightness needs 0-100 or 'auto'.")
                Settings.System.putInt(
                    cr, Settings.System.SCREEN_BRIGHTNESS_MODE,
                    Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL
                )
                Settings.System.putInt(
                    cr, Settings.System.SCREEN_BRIGHTNESS,
                    (percent.coerceIn(0, 100) * 255 / 100.0).roundToInt()
                )
                return ok("Brightness set to $percent%.")
            }
            "font_size" -> {
                if (!canWriteSystem(ctx)) return needsSystemWrite("text size")
                val scale = (value as? Number)?.toFloat() ?: value.toString().toFloatOrNull()
                    ?: return fail("Text size needs a scale like 1.15.")
                Settings.System.putFloat(cr, Settings.System.FONT_SCALE, scale.coerceIn(0.5f, 2.0f))
                return ok("Text size set to ${scale}x.")
            }
            "screen_timeout" -> {
                if (!canWriteSystem(ctx)) return needsSystemWrite("the screen timeout")
                val seconds = (value as? Number)?.toInt() ?: value.toString().toIntOrNull()
                    ?: return fail("Screen timeout needs a number of seconds.")
                Settings.System.putInt(cr, Settings.System.SCREEN_OFF_TIMEOUT, seconds * 1000)
                return ok("The screen now turns off after $seconds seconds.")
            }
            "auto_rotate" -> {
                if (!canWriteSystem(ctx)) return needsSystemWrite("auto-rotate")
                Settings.System.putInt(cr, Settings.System.ACCELEROMETER_ROTATION, if (on) 1 else 0)
                return ok("Auto-rotate turned ${if (on) "on" else "off"}.")
            }
            // Android does not let ordinary apps flip these. The panel with
            // the switch is one tap away instead.
            "wifi" -> return opened(
                ctx, Settings.Panel.ACTION_WIFI, "Wi-Fi",
                "Android does not let apps switch Wi-Fi directly. Opened the Wi-Fi panel - tap " +
                    "the switch to turn it ${if (on) "on" else "off"}."
            )
            "mobile_data", "airplane_mode" -> return opened(
                ctx, Settings.Panel.ACTION_INTERNET_CONNECTIVITY, "internet",
                "Android does not let apps switch ${name.replace('_', ' ')} directly. Opened the " +
                    "internet panel - tap the switch there."
            )
            "bluetooth" -> return opened(
                ctx, Settings.ACTION_BLUETOOTH_SETTINGS, "Bluetooth",
                "Android does not let apps switch Bluetooth directly. Opened the Bluetooth " +
                    "settings - tap the switch there."
            )
            "location" -> return opened(
                ctx, Settings.ACTION_LOCATION_SOURCE_SETTINGS, "location",
                "Android does not let apps switch location directly. Opened the location " +
                    "settings - tap the switch there."
            )
        }
        return fail("Unknown setting '$name'.")
    }
}
