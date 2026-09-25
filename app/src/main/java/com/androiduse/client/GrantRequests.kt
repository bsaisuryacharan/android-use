package com.androiduse.client

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject

/**
 * Asking the owner for control, with one tap to say yes.
 *
 * When a grant lapses, the old way back was to phone the owner and talk them
 * through opening the app and finding the right button - exactly the kind of
 * thing this app exists to spare them. Now the helper asks, and a
 * notification offers "Allow for 1 hour" and "Not now". Nothing is granted
 * without that tap.
 */
object GrantRequests {

    const val ACTION_ALLOW = "com.androiduse.client.GRANT_ALLOW"
    const val ACTION_DENY = "com.androiduse.client.GRANT_DENY"
    const val EXTRA_MINUTES = "minutes"
    private const val CHANNEL = "android_use_requests"
    const val NOTIFICATION_ID = 4474

    fun request(ctx: Context, minutes: Int, reason: String): JSONObject {
        if (Security.isGranted(ctx)) return JSONObject().put("ok", true).put("already_granted", true)
        if (!Security.mayRequestGrant(ctx)) return JSONObject().put("ok", false).put("throttled", true)

        val manager = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Requests for help", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Shown when your helper asks to control this phone."
            }
        )
        val mins = minutes.coerceIn(5, 24 * 60)
        val allow = PendingIntent.getBroadcast(
            ctx, 10,
            Intent(ctx, GrantReceiver::class.java).setAction(ACTION_ALLOW).putExtra(EXTRA_MINUTES, mins),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val deny = PendingIntent.getBroadcast(
            ctx, 11,
            Intent(ctx, GrantReceiver::class.java).setAction(ACTION_DENY),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val open = PendingIntent.getActivity(
            ctx, 12, Intent(ctx, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val duration = if (mins % 60 == 0) "${mins / 60} hour${if (mins == 60) "" else "s"}" else "$mins minutes"
        val why = reason.trim().take(200)
        val body = "Your helper is asking to use this phone for $duration." +
            if (why.isNotEmpty()) "\n\nReason: $why" else ""
        manager.notify(
            NOTIFICATION_ID,
            Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_menu_help)
                .setContentTitle("Allow your helper to use this phone?")
                .setContentText(body.lineSequence().first())
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setContentIntent(open)
                .setAutoCancel(true)
                .addAction(Notification.Action.Builder(null, "Allow for $duration", allow).build())
                .addAction(Notification.Action.Builder(null, "Not now", deny).build())
                .build()
        )
        ActivityLog.add(ctx, "Helper asked for control ($duration)${if (why.isNotEmpty()) ": $why" else ""}")
        return JSONObject().put("ok", true).put("pending", true)
    }
}

/** Receives the owner's answer from the notification. Not exported: only the
 *  notification's own PendingIntent can reach it. */
class GrantReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.cancel(GrantRequests.NOTIFICATION_ID)
        when (intent.action) {
            GrantRequests.ACTION_ALLOW -> {
                val minutes = intent.getIntExtra(GrantRequests.EXTRA_MINUTES, 60)
                Security.grantFor(context, minutes)
                ActivityLog.add(context, "You allowed help for $minutes minutes")
                BridgeService.refresh(context)
                BridgeService.start(context)
            }
            GrantRequests.ACTION_DENY -> ActivityLog.add(context, "You declined a request for help")
        }
    }
}
