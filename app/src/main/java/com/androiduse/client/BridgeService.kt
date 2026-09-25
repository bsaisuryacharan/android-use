package com.androiduse.client

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper

/**
 * Keeps the bridge alive and, just as importantly, keeps it visible.
 *
 * The notification is not a formality: it is the only way the phone's owner can
 * see that something can currently control their device, and stop it.
 */
class BridgeService : Service() {

    private var bridge: HttpBridge? = null
    private val handler = Handler(Looper.getMainLooper())

    /**
     * Watches our own accessibility service.
     *
     * Android revokes it from apps it did not install. With WRITE_SECURE_SETTINGS
     * the app puts itself back silently. Without it - which is the case when the
     * phone was set up without a cable - nobody can fix it remotely, so the only
     * honest thing is to tell the person holding the phone, clearly, and take
     * them straight to the switch.
     */
    private val selfCheck = object : Runnable {
        override fun run() {
            val off = !Setup.accessibilityEnabled(applicationContext)
            if (off && !Setup.canSelfRepair(applicationContext)) {
                notifyNeedsAttention()
            } else if (!off) {
                clearAttention()
            }
            // Keeps the "minutes left" in the notification honest, and drops
            // the "can control" wording the moment a grant runs out.
            refresh(applicationContext)
            handler.postDelayed(this, CHECK_INTERVAL_MS)
        }
    }

    companion object {
        const val CHANNEL = "android_use_bridge"
        const val NOTIFICATION_ID = 4471
        const val ACTION_STOP = "com.androiduse.client.STOP"
        const val ATTENTION_ID = 4472
        const val ATTENTION_CHANNEL = "android_use_attention"
        const val CHECK_INTERVAL_MS = 60_000L

        fun start(context: Context) {
            val intent = Intent(context, BridgeService::class.java)
            runCatching {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            }
        }

        /** Update the notification in place - after a grant, a stop, or a tick. */
        fun refresh(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            createChannel(context)
            runCatching { manager.notify(NOTIFICATION_ID, build(context)) }
        }

        private fun createChannel(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val channel = NotificationChannel(
                CHANNEL, "Remote control", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "Shown whenever this phone can be controlled remotely." }
            context.getSystemService(NotificationManager::class.java)?.createNotificationChannel(channel)
        }

        fun build(context: Context): Notification {
            val granted = Security.isGranted(context)
            val minutes = Security.grantRemainingMs(context) / 60_000
            val open = PendingIntent.getActivity(
                context, 0, Intent(context, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            val stop = PendingIntent.getService(
                context, 1,
                Intent(context, BridgeService::class.java).setAction(ACTION_STOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            val left = when {
                minutes >= 120 -> "about ${minutes / 60} hours left"
                minutes >= 1 -> "$minutes min left"
                else -> "less than a minute left"
            }
            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(context, CHANNEL)
            } else {
                @Suppress("DEPRECATION") Notification.Builder(context)
            }
            return builder
                .setContentTitle(
                    if (granted) "Your assistant can control this phone"
                    else "Android Use is ready (control not granted)"
                )
                .setContentText(
                    if (granted) "$left. Tap Stop to end it immediately."
                    else "Open the app to grant access for a limited time."
                )
                .setSmallIcon(android.R.drawable.ic_menu_manage)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(open)
                .addAction(
                    Notification.Action.Builder(null, "Stop", stop).build()
                )
                .build()
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel(this)
        startForeground(NOTIFICATION_ID, build(this))
        bridge = HttpBridge(applicationContext).also { it.start() }
        handler.postDelayed(selfCheck, 5_000L)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Security.revoke(applicationContext)
            ControlAccessibilityService.instance?.overlays?.removeAll()
            ActivityLog.add(applicationContext, "You stopped the help")
            refresh(applicationContext)
            stopSelf()
            return START_NOT_STICKY
        }
        refresh(applicationContext)
        // START_STICKY so Funtouch's aggressive process management does not
        // silently end the session; the accessibility binding also revives it.
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacks(selfCheck)
        bridge?.stop()
        bridge = null
        super.onDestroy()
    }

    private fun notifyNeedsAttention() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(
                    ATTENTION_CHANNEL, "Needs attention",
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "Shown when help has stopped working and you need to switch it back on."
                }
            )
        }
        val open = PendingIntent.getActivity(
            this, 2, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, ATTENTION_CHANNEL)
        } else {
            @Suppress("DEPRECATION") Notification.Builder(this)
        }
        manager.notify(
            ATTENTION_ID,
            builder
                .setContentTitle("Help has stopped working")
                .setContentText("Tap here, then turn \"Android Use\" back on.")
                .setStyle(
                    Notification.BigTextStyle().bigText(
                        "Android switched off the setting that lets your helper see " +
                            "this screen. Tap here and follow step 1 to turn it back on."
                    )
                )
                .setSmallIcon(android.R.drawable.stat_notify_error)
                .setContentIntent(open)
                .setAutoCancel(true)
                .setOngoing(true)
                .build()
        )
    }

    private fun clearAttention() {
        getSystemService(NotificationManager::class.java)?.cancel(ATTENTION_ID)
    }
}
