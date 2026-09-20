package com.androiduse.client

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * A floating, draggable panel that shows the Claude chat over whatever app is
 * on screen.
 *
 * This exists to fix the same-device problem: controlling this phone from the
 * Claude app *on* this phone means Claude keeps reading the Claude app instead
 * of the thing you want to control. With the chat floating in a small window,
 * the app you are working on stays in front - so Claude reads that - while you
 * still see and type to Claude.
 */
class OverlayService : Service() {

    private lateinit var wm: WindowManager
    private var root: View? = null
    private var webView: WebView? = null
    private lateinit var params: WindowManager.LayoutParams

    companion object {
        const val CHANNEL = "android_use_overlay"
        const val NOTIF_ID = 4473
        const val ACTION_STOP = "com.androiduse.client.OVERLAY_STOP"
        const val CLAUDE_URL = "https://claude.ai"

        // So the accessibility service can hide this window from what Claude
        // reads - the overlay must never appear in the screen it is driving.
        @Volatile
        var showing = false
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf(); return START_NOT_STICKY
        }
        if (root == null) build()
        return START_STICKY
    }

    override fun onCreate() {
        super.onCreate()
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        startForeground(NOTIF_ID, notification())
    }

    private fun build() {
        val metrics = resources.displayMetrics
        val width = (metrics.widthPixels * 0.92).toInt()
        // ~38% of the screen height - enough to read the chat, small enough to
        // leave the controlled app usable underneath.
        val height = (metrics.heightPixels * 0.42).toInt()

        params = WindowManager.LayoutParams(
            width, height,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else
                @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE,
            // Not focusable by default: the app underneath keeps input focus,
            // so Claude reads that app and not this panel. Not-touch-modal lets
            // taps outside the panel reach the app. Focus is turned on only
            // while the user is actually typing (the ⌨ toggle below).
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = (metrics.widthPixels - width) / 2
            y = metrics.heightPixels - height - 120
        }

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(0xEE1A1A1A.toInt())
        }
        container.addView(dragBar(), ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(44)))

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.setSupportZoom(true)
            settings.builtInZoomControls = true
            settings.displayZoomControls = false
            CookieManager.getInstance().setAcceptCookie(true)
            CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
            webViewClient = WebViewClient()
            webChromeClient = WebChromeClient()
            loadUrl(CLAUDE_URL)
        }
        container.addView(webView, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        root = container
        wm.addView(root, params)
        showing = true
    }

    /** Title strip: drag to move, buttons to resize and close. */
    private fun dragBar(): View {
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(0xFF2C2C2C.toInt())
            setPadding(dp(14), 0, dp(6), 0)
        }
        val label = TextView(this).apply {
            text = "Claude  ⠿  drag to move"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            layoutParams = LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        typingLabel = label
        bar.addView(label)
        bar.addView(barButton("⌨") { toggleTyping() })
        bar.addView(barButton("–") { resize(0.72f) })
        bar.addView(barButton("+") { resize(1.30f) })
        bar.addView(barButton("✕") { stopSelf() })

        // Drag handling on the bar moves the whole window.
        var startX = 0; var startY = 0; var touchX = 0f; var touchY = 0f
        bar.setOnTouchListener { _, e ->
            when (e.action) {
                MotionEvent.ACTION_DOWN -> {
                    startX = params.x; startY = params.y
                    touchX = e.rawX; touchY = e.rawY; true
                }
                MotionEvent.ACTION_MOVE -> {
                    params.x = startX + (e.rawX - touchX).toInt()
                    params.y = startY + (e.rawY - touchY).toInt()
                    wm.updateViewLayout(root, params); true
                }
                else -> false
            }
        }
        return bar
    }

    private var typing = false

    /**
     * Switch between reading and typing.
     *
     * View mode (default): the panel takes no focus, so Claude reads the app
     * underneath. Type mode: the panel takes focus so the keyboard works and
     * you can write to Claude - but while it is on, Claude would read the panel,
     * so switch back to view mode before asking Claude to act.
     */
    private fun toggleTyping() {
        typing = !typing
        params.flags = if (typing) {
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        } else {
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        }
        wm.updateViewLayout(root, params)
        typingLabel?.text = if (typing) "TYPING - tap ⌨ when done" else "Claude  ⠿  drag to move"
        if (typing) webView?.requestFocus()
    }

    private var typingLabel: TextView? = null

    private fun resize(factor: Float) {
        val metrics = resources.displayMetrics
        params.height = (params.height * factor).toInt()
            .coerceIn(dp(160), (metrics.heightPixels * 0.85).toInt())
        wm.updateViewLayout(root, params)
    }

    private fun barButton(label: String, onClick: () -> Unit) =
        Button(this).apply {
            text = label
            setTextColor(Color.WHITE)
            setBackgroundColor(0x00000000)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            minWidth = dp(40); minimumWidth = dp(40)
            setPadding(dp(8), 0, dp(8), 0)
            setOnClickListener { onClick() }
        }

    private fun dp(v: Int): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(NotificationChannel(
                CHANNEL, "Floating panel", NotificationManager.IMPORTANCE_LOW))
        }
        val stop = PendingIntent.getService(
            this, 0, Intent(this, OverlayService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(this, CHANNEL)
        else @Suppress("DEPRECATION") Notification.Builder(this)
        return b.setContentTitle("Claude panel is floating")
            .setContentText("Tap to hide it.")
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Hide", stop).build())
            .build()
    }

    override fun onDestroy() {
        showing = false
        root?.let { runCatching { wm.removeView(it) } }
        webView?.destroy()
        root = null; webView = null
        super.onDestroy()
    }
}
