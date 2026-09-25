package com.androiduse.client

import android.accessibilityservice.AccessibilityService
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * What the owner sees while someone else is using their phone.
 *
 * - A small banner, "Your assistant is using this phone", with a Stop button,
 *   shown while commands are arriving. It also keeps the screen awake, so the
 *   phone does not lock itself while the assistant is thinking between steps.
 * - A brief ring wherever a tap lands, so actions are visible rather than
 *   the phone seeming to move by itself.
 * - Large, plain message and question cards, for talking to the owner.
 *
 * All of these are accessibility overlays: they need no extra permission,
 * and they never appear in the screen the assistant reads.
 */
class Overlays(private val service: AccessibilityService) {

    companion object {
        /** How long the banner (and the screen) stays up after the last command. */
        const val BANNER_IDLE_MS = 120_000L
    }

    private val wm = service.getSystemService(AccessibilityService.WINDOW_SERVICE) as WindowManager
    private val main = Handler(Looper.getMainLooper())
    private var banner: View? = null
    private var message: View? = null
    private var question: View? = null
    private val hideBanner = Runnable { remove(banner); banner = null }
    private val hideMessage = Runnable { remove(message); message = null }

    private fun dp(v: Int): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), service.resources.displayMetrics
    ).toInt()

    private fun statusBarHeight(): Int {
        val id = service.resources.getIdentifier("status_bar_height", "dimen", "android")
        return if (id > 0) service.resources.getDimensionPixelSize(id) else dp(24)
    }

    private fun screenWidth(): Int = wm.maximumWindowMetrics.bounds.width()

    private fun params(width: Int, height: Int, touchable: Boolean, keepOn: Boolean) =
        WindowManager.LayoutParams(
            width, height,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                (if (touchable) 0 else WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE) or
                (if (keepOn) WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON else 0),
            PixelFormat.TRANSLUCENT
        ).apply {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        }

    private fun remove(view: View?) {
        if (view != null) runCatching { wm.removeView(view) }
    }

    /** Run on the main thread and wait for it - window changes must happen there. */
    private fun <T> onMain(block: () -> T): T? {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        val result = AtomicReference<T?>(null)
        val latch = CountDownLatch(1)
        main.post {
            try { result.set(block()) } finally { latch.countDown() }
        }
        latch.await(2, TimeUnit.SECONDS)
        return result.get()
    }

    // ------------------------------------------------------------- banner

    /** Called for every command: show the banner and push back its timeout. */
    fun activity() {
        main.post {
            if (banner == null) addBanner()
            main.removeCallbacks(hideBanner)
            main.postDelayed(hideBanner, BANNER_IDLE_MS)
        }
    }

    private fun addBanner() {
        val row = LinearLayout(service).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(2), dp(4), dp(2))
            background = GradientDrawable().apply {
                cornerRadius = dp(20).toFloat()
                setColor(0xE6202124.toInt())
            }
        }
        row.addView(TextView(service).apply {
            text = "●  Your assistant is using this phone"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        })
        row.addView(Button(service).apply {
            text = "Stop"
            isAllCaps = false
            setTextColor(0xFFFF8A80.toInt())
            setBackgroundColor(Color.TRANSPARENT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setTypeface(typeface, Typeface.BOLD)
            setOnClickListener { stopByOwner() }
        })
        val p = params(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            touchable = true, keepOn = true
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = statusBarHeight() + dp(4)
        }
        if (runCatching { wm.addView(row, p) }.isSuccess) banner = row
    }

    private fun stopByOwner() {
        Security.revoke(service)
        ActivityLog.add(service, "You stopped the help")
        removeAll()
        BridgeService.refresh(service)
        Toast.makeText(service, "Stopped. Your helper can no longer use this phone.", Toast.LENGTH_LONG).show()
    }

    // --------------------------------------------------------------- taps

    /** A ring where a tap landed, fading out - so remote taps are visible. */
    fun ripple(x: Float, y: Float) {
        main.post {
            val size = dp(46)
            val ring = View(service).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setStroke(dp(3), 0xFF4FC3F7.toInt())
                    setColor(0x334FC3F7)
                }
            }
            val p = params(size, size, touchable = false, keepOn = false).apply {
                gravity = Gravity.TOP or Gravity.START
                this.x = (x - size / 2f).toInt()
                this.y = (y - size / 2f).toInt()
            }
            if (runCatching { wm.addView(ring, p) }.isSuccess) {
                ring.animate().alpha(0f).scaleX(1.7f).scaleY(1.7f).setDuration(500)
                    .withEndAction { remove(ring) }.start()
            }
        }
    }

    /**
     * Let an injected gesture through the overlays.
     *
     * The banner and cards take touches (the Stop button must work), so a
     * gesture aimed at a spot under one of them would hit the overlay instead
     * of the app. For the length of the gesture they stop taking touches.
     */
    fun <T> passThrough(block: () -> T): T {
        setTouchable(false)
        try {
            return block()
        } finally {
            setTouchable(true)
        }
    }

    private fun setTouchable(touchable: Boolean) {
        onMain {
            for (view in listOfNotNull(banner, message, question)) {
                val p = view.layoutParams as? WindowManager.LayoutParams ?: continue
                p.flags = if (touchable) p.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE.inv()
                else p.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                runCatching { wm.updateViewLayout(view, p) }
            }
        }
    }

    /** Take the overlays out of a screenshot, so it shows only the phone. */
    fun <T> hiddenForCapture(block: () -> T): T {
        val views = onMain {
            listOfNotNull(banner, message).onEach { it.visibility = View.INVISIBLE }
        }.orEmpty()
        if (views.isNotEmpty()) Thread.sleep(120)  // let a frame without them reach the screen
        try {
            return block()
        } finally {
            onMain { views.forEach { it.visibility = View.VISIBLE } }
        }
    }

    // ------------------------------------------------------ talking to owner

    private fun card(title: String, body: String): LinearLayout =
        LinearLayout(service).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(18), dp(22), dp(14))
            background = GradientDrawable().apply {
                cornerRadius = dp(18).toFloat()
                setColor(Color.WHITE)
            }
            elevation = dp(8).toFloat()
            addView(TextView(service).apply {
                text = title
                setTextColor(0xFF5F6368.toInt())
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            })
            addView(TextView(service).apply {
                text = body
                setTextColor(0xFF202124.toInt())
                // Big on purpose: this is often read by someone who struggles
                // with small print.
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f)
                setPadding(0, dp(8), 0, dp(12))
            })
        }

    private fun bigButton(label: String, primary: Boolean, onClick: () -> Unit) =
        Button(service).apply {
            text = label
            isAllCaps = false
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 19f)
            setTextColor(if (primary) Color.WHITE else 0xFF1A73E8.toInt())
            background = GradientDrawable().apply {
                cornerRadius = dp(26).toFloat()
                if (primary) setColor(0xFF1A73E8.toInt()) else setStroke(dp(2), 0xFF1A73E8.toInt())
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56)
            ).apply { topMargin = dp(10) }
            setOnClickListener { onClick() }
        }

    /** A message for the owner, near the top, with an OK button. It goes away
     *  by itself, and whenever the assistant makes its next gesture. */
    fun message(text: String) {
        main.post {
            remove(message)
            val box = card("Message from your helper", text)
            box.addView(bigButton("OK", primary = true) { remove(message); message = null })
            val p = params(
                screenWidth() - dp(24), ViewGroup.LayoutParams.WRAP_CONTENT,
                touchable = true, keepOn = true
            ).apply {
                gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
                y = statusBarHeight() + dp(48)
            }
            if (runCatching { wm.addView(box, p) }.isSuccess) {
                message = box
                main.removeCallbacks(hideMessage)
                main.postDelayed(hideMessage, (10_000L + text.length * 60L).coerceAtMost(45_000L))
            }
        }
    }

    fun dismissMessage() {
        main.post { hideMessage.run() }
    }

    /**
     * Ask the owner and wait for a tap on one of the options.
     * Blocks the calling (bridge) thread; returns null if nobody answers.
     */
    fun ask(question: String, options: List<String>, timeoutMs: Long): String? {
        val answer = AtomicReference<String?>(null)
        val latch = CountDownLatch(1)
        main.post {
            remove(this.question)
            val scrim = FrameLayout(service).apply { setBackgroundColor(0x99000000.toInt()) }
            val box = card("Your helper is asking", question)
            options.forEachIndexed { i, option ->
                box.addView(bigButton(option, primary = i == 0) {
                    answer.set(option)
                    latch.countDown()
                })
            }
            scrim.addView(
                box,
                FrameLayout.LayoutParams(
                    screenWidth() - dp(32), ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER
                )
            )
            val p = params(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT,
                touchable = true, keepOn = true
            )
            if (runCatching { wm.addView(scrim, p) }.isSuccess) this.question = scrim
            else latch.countDown()
        }
        latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        main.post { remove(this.question); this.question = null }
        return answer.get()
    }

    fun removeAll() {
        main.post {
            main.removeCallbacks(hideBanner)
            main.removeCallbacks(hideMessage)
            remove(banner); remove(message); remove(question)
            banner = null; message = null; question = null
        }
    }
}
