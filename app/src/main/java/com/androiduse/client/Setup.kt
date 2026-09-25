package com.androiduse.client

import android.accessibilityservice.AccessibilityServiceInfo
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import java.net.NetworkInterface

/**
 * The onboarding checklist.
 *
 * The person running this may be doing it alone, on the phone to someone else,
 * and may not be comfortable with settings screens. So every step reports its
 * own state, and each one can open exactly the screen it needs - no hunting
 * through menus, no jargon.
 */
object Setup {

    data class Step(
        val title: String,
        val why: String,
        val done: Boolean,
        val actionLabel: String,
        val action: (Context) -> Unit,
        val hint: String = "",
    )

    fun accessibilityEnabled(ctx: Context): Boolean {
        val manager = ctx.getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
        return manager
            .getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
            .any { it.resolveInfo?.serviceInfo?.packageName == ctx.packageName }
    }

    /**
     * Can the app show notifications? On Android 13+ this is a permission the
     * owner grants, and without it the "someone can control this phone - Stop"
     * notification is silently hidden. That notification is a promise this
     * app makes, so it is a setup step, not an optional extra.
     */
    fun notificationsAllowed(ctx: Context): Boolean {
        val manager = ctx.getSystemService(NotificationManager::class.java) ?: return false
        return manager.areNotificationsEnabled()
    }

    fun batteryExempt(ctx: Context): Boolean {
        val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isIgnoringBatteryOptimizations(ctx.packageName)
    }

    fun tailscaleInstalled(ctx: Context): Boolean =
        runCatching { ctx.packageManager.getPackageInfo("com.tailscale.ipn", 0) }.isSuccess

    /** A 100.x address means Tailscale is actually connected, not merely installed. */
    fun tailscaleAddress(): String {
        runCatching {
            NetworkInterface.getNetworkInterfaces().toList().forEach { nif ->
                if (!nif.isUp) return@forEach
                nif.inetAddresses.toList().forEach { addr ->
                    val ip = addr.hostAddress ?: return@forEach
                    if (ip.startsWith("100.") && !ip.contains(":")) return ip
                }
            }
        }
        return ""
    }

    /** Only meaningful once a cable has been used; shown so its absence is visible. */
    fun canSelfRepair(ctx: Context): Boolean =
        ctx.checkCallingOrSelfPermission(
            android.Manifest.permission.WRITE_SECURE_SETTINGS
        ) == android.content.pm.PackageManager.PERMISSION_GRANTED


    /**
     * Is Tailscale set as the always-on VPN?
     *
     * This matters more than it looks: without it, a reboot can leave the phone
     * with no Tailscale at all - no connection, no watchdog recovery, no remote
     * fix. Someone would have to open an app they have no reason to know about.
     */
    fun alwaysOnVpnSet(ctx: Context): Boolean {
        // Android restricts which Settings.Secure keys an ordinary app may
        // read, and always_on_vpn_app is one it can be refused. Relying on it
        // alone left this step permanently unticked even once the user had
        // done it - a checklist item that can never go green is worse than no
        // checklist item, so an explicit acknowledgement stands in.
        val reported = runCatching {
            Settings.Secure.getString(ctx.contentResolver, "always_on_vpn_app")
        }.getOrNull()
        if (reported == "com.tailscale.ipn") return true
        return ctx.getSharedPreferences("android_use_security", Context.MODE_PRIVATE)
            .getBoolean("always_on_acknowledged", false)
    }

    fun acknowledgeAlwaysOn(ctx: Context, value: Boolean = true) {
        ctx.getSharedPreferences("android_use_security", Context.MODE_PRIVATE)
            .edit().putBoolean("always_on_acknowledged", value).apply()
    }

    fun steps(ctx: Context): List<Step> = listOf(
        Step(
            title = "Let the assistant see this screen",
            why = "Without this it cannot read the screen or tap anything.",
            done = accessibilityEnabled(ctx),
            actionLabel = "Open accessibility settings",
            action = { c -> c.startActivity(
                Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ) },
            // Wording checked against a real sideloaded install on Android 16:
            // tapping the switch shows "App was denied access" with only a
            // Close button, which gives no hint about what to do next.
            hint = "Tap the button below, then tap \"Android Use control\" " +
                "and turn it on.\n\n" +
                "IF YOU SEE \"App was denied access\":\n" +
                "  1. Tap Close\n" +
                "  2. Come back here and tap \"App info\"\n" +
                "  3. Tap the three dots at the top right\n" +
                "  4. Tap \"Allow restricted settings\"\n" +
                "  5. Then try the accessibility button again\n\n" +
                "This happens because the app was sent to you directly rather " +
                "than downloaded from the Play Store. It is normal.",
        ),
        Step(
            title = "Show when help is active",
            why = "So you can always see when someone can use your phone, and stop it with one tap.",
            done = notificationsAllowed(ctx),
            actionLabel = "Allow notifications",
            action = { c ->
                val activity = c as? android.app.Activity
                // The system prompt can only be shown so many times; after the
                // first try, the app's notification settings are the way in.
                if (activity != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                    !askedForNotifications(c)
                ) {
                    markAskedForNotifications(c)
                    activity.requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 7)
                } else {
                    c.startActivity(
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, c.packageName)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                }
            },
        ),
        Step(
            title = "Keep it running in the background",
            why = "Otherwise the phone shuts it down and help stops working.",
            done = batteryExempt(ctx),
            actionLabel = "Allow background running",
            action = { c ->
                c.startActivity(
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                        .setData(Uri.parse("package:${c.packageName}"))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            },
        ),
        Step(
            title = "Install Tailscale",
            why = "This is how the phone can be reached from anywhere, on mobile data.",
            done = tailscaleInstalled(ctx),
            actionLabel = "Get Tailscale",
            action = { c ->
                val intent = Intent(Intent.ACTION_VIEW)
                    .setData(Uri.parse("market://details?id=com.tailscale.ipn"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                runCatching { c.startActivity(intent) }.onFailure {
                    c.startActivity(
                        Intent(Intent.ACTION_VIEW, Uri.parse(
                            "https://play.google.com/store/apps/details?id=com.tailscale.ipn"
                        )).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                }
            },
        ),
        Step(
            title = "Connect Tailscale",
            why = "Sign in with the account your helper gave you, then come back.",
            done = tailscaleAddress().isNotBlank(),
            actionLabel = "Open Tailscale",
            action = { c ->
                val launch = c.packageManager.getLaunchIntentForPackage("com.tailscale.ipn")
                if (launch != null) c.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            },
        ),
        Step(
            title = "Keep the connection after a restart",
            why = "Without this, a restart can leave the phone unreachable and " +
                "nobody can fix it remotely.",
            done = alwaysOnVpnSet(ctx),
            actionLabel = "Open VPN settings",
            action = { c ->
                c.startActivity(
                    Intent(Settings.ACTION_VPN_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            },
            hint = "Tap the settings icon next to \"Tailscale\", then turn on " +
                "\"Always-on VPN\".\n\nThis makes Tailscale start by itself " +
                "whenever the phone restarts.",
        ),
    )

    fun openAppInfo(ctx: Context) {
        ctx.startActivity(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:${ctx.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    private fun askedForNotifications(ctx: Context): Boolean =
        ctx.getSharedPreferences("android_use_security", Context.MODE_PRIVATE)
            .getBoolean("asked_notifications", false)

    private fun markAskedForNotifications(ctx: Context) {
        ctx.getSharedPreferences("android_use_security", Context.MODE_PRIVATE)
            .edit().putBoolean("asked_notifications", true).apply()
    }

    fun allReady(ctx: Context): Boolean =
        accessibilityEnabled(ctx) &&
            notificationsAllowed(ctx) &&
            tailscaleAddress().isNotBlank() &&
            alwaysOnVpnSet(ctx)
}
