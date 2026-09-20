package com.androiduse.client

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Reads the screen and acts on it.
 *
 * This is the same idea as the ADB backend's uiautomator parsing, but done
 * in-process: the tree is live rather than a dump, so there is no race with
 * animations, and nodes can be clicked directly instead of by coordinate.
 */
class ControlAccessibilityService : AccessibilityService() {

    companion object {
        @Volatile
        var instance: ControlAccessibilityService? = null

        const val MAX_ELEMENTS = 120
    }

    /** Nodes from the last read, so the bridge can act on them by index. */
    private val indexed = mutableListOf<AccessibilityNodeInfo>()

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        BridgeService.start(this)
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) { /* polled, not pushed */ }
    override fun onInterrupt() {}

    // ---------------------------------------------------------------- reading

    private fun isActionable(node: AccessibilityNodeInfo): Boolean =
        node.isClickable || node.isCheckable || node.isEditable

    /** Collect the text a person would read off this subtree. */
    private fun labelOf(node: AccessibilityNodeInfo): String {
        val parts = mutableListOf<String>()
        fun walk(n: AccessibilityNodeInfo?, depth: Int) {
            if (n == null || depth > 6) return
            // Text inside a nested actionable node belongs to that node, not this one.
            if (depth > 0 && isActionable(n)) return
            n.text?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { parts.add(it) }
            for (i in 0 until n.childCount) walk(n.getChild(i), depth + 1)
        }
        walk(node, 0)
        if (parts.isEmpty()) {
            node.contentDescription?.toString()?.trim()
                ?.takeIf { it.isNotEmpty() }?.let { parts.add(it) }
        }
        return parts.distinct().joinToString(" / ").take(110)
    }

    private fun checkableState(node: AccessibilityNodeInfo): Pair<Boolean, Boolean> {
        if (node.isCheckable) return true to node.isChecked
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            if (child.isCheckable) return true to child.isChecked
        }
        return false to false
    }

    /** Snapshot the screen as the same shape the Python side already understands. */
    /**
     * The window Claude should read.
     *
     * Normally the active window. But when our own floating panel is up, the
     * active window may be the panel - and Claude must read the app being
     * controlled, never itself. So if the active window is ours, fall back to
     * the largest application window that is not ours.
     */
    private fun windowToRead(): AccessibilityNodeInfo? {
        val active = rootInActiveWindow
        if (active != null && active.packageName != packageName) return active
        // Active window is ours (overlay or setup screen). Find the real app.
        var best: AccessibilityNodeInfo? = active
        var bestArea = -1
        for (w in windows) {
            val node = w.root ?: continue
            if (node.packageName == packageName) continue
            val b = Rect().also { node.getBoundsInScreen(it) }
            val area = b.width() * b.height()
            if (area > bestArea) { bestArea = area; best = node }
        }
        return best
    }

    fun readScreen(): JSONObject {
        val root = windowToRead()
            ?: return JSONObject().put("error", "No window is readable right now.")

        indexed.forEach { runCatching { it.recycle() } }
        indexed.clear()

        val elements = JSONArray()
        val looseText = JSONArray()
        var scrollable: Rect? = null

        fun visit(node: AccessibilityNodeInfo?, insideActionable: Boolean) {
            if (node == null || elements.length() >= MAX_ELEMENTS) return
            val bounds = Rect().also { node.getBoundsInScreen(it) }
            val onScreen = bounds.width() > 0 && bounds.height() > 0
            if (node.isScrollable && onScreen) {
                if (scrollable == null || bounds.width() * bounds.height() >
                    scrollable!!.width() * scrollable!!.height()
                ) scrollable = Rect(bounds)
            }
            var nowInside = insideActionable
            // Skip our own floating panel; Claude drives the app under it.
            if (node.packageName == packageName && OverlayService.showing) {
                for (i in 0 until node.childCount) visit(node.getChild(i), insideActionable)
                return
            }
            if (onScreen && node.isVisibleToUser && isActionable(node)) {
                val (checkable, checked) = checkableState(node)
                elements.put(
                    JSONObject()
                        .put("index", indexed.size)
                        .put("label", labelOf(node))
                        .put("cls", node.className?.toString()?.substringAfterLast('.') ?: "item")
                        .put("resource_id", node.viewIdResourceName ?: "")
                        .put("editable", node.isEditable)
                        .put("scrollable", node.isScrollable)
                        .put("enabled", node.isEnabled)
                        .put("checkable", checkable)
                        .put("checked", checked)
                        .put(
                            "bounds",
                            JSONArray(listOf(bounds.left, bounds.top, bounds.right, bounds.bottom))
                        )
                )
                indexed.add(AccessibilityNodeInfo.obtain(node))
                nowInside = true
            } else if (!insideActionable && onScreen && node.isVisibleToUser) {
                node.text?.toString()?.trim()?.takeIf { it.isNotEmpty() && it.length < 200 }
                    ?.let { if (looseText.length() < 15) looseText.put(it) }
            }
            for (i in 0 until node.childCount) visit(node.getChild(i), nowInside)
        }
        visit(root, false)

        val metrics = resources.displayMetrics
        return JSONObject()
            .put("package", root.packageName?.toString() ?: "")
            .put("activity", "")
            .put("elements", elements)
            .put("texts", looseText)
            .put("width", metrics.widthPixels)
            .put("height", metrics.heightPixels)
            .put(
                "scroll_region",
                scrollable?.let { JSONArray(listOf(it.left, it.top, it.right, it.bottom)) }
                    ?: JSONObject.NULL
            )
    }

    // ---------------------------------------------------------------- acting

    fun nodeAt(index: Int): AccessibilityNodeInfo? = indexed.getOrNull(index)

    /** Click a node directly, walking up to a clickable parent if needed. */
    fun clickIndex(index: Int): Boolean {
        var node = nodeAt(index) ?: return false
        var hops = 0
        while (hops < 5) {
            if (node.isClickable && node.isEnabled) {
                return node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            }
            node = node.parent ?: return false
            hops++
        }
        return false
    }

    /** Type any script or emoji - the thing `adb shell input text` cannot do. */
    fun setTextAt(index: Int, text: String): Boolean {
        val node = nodeAt(index) ?: return false
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    fun scrollIndex(index: Int, forward: Boolean): Boolean {
        val node = nodeAt(index) ?: return false
        val action = if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
        else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
        return node.performAction(action)
    }

    fun globalAction(name: String): Boolean {
        val action = when (name.lowercase()) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings" -> GLOBAL_ACTION_QUICK_SETTINGS
            "lock" -> GLOBAL_ACTION_LOCK_SCREEN
            else -> return false
        }
        return performGlobalAction(action)
    }

    /** Gestures ADB could not do: long press, pinch, drag, multi-touch. */
    fun gesture(strokes: List<Triple<Path, Long, Long>>): Boolean {
        val builder = GestureDescription.Builder()
        strokes.forEach { (path, start, duration) ->
            builder.addStroke(GestureDescription.StrokeDescription(path, start, duration))
        }
        val latch = CountDownLatch(1)
        var ok = false
        val dispatched = dispatchGesture(
            builder.build(),
            object : GestureResultCallback() {
                override fun onCompleted(d: GestureDescription?) { ok = true; latch.countDown() }
                override fun onCancelled(d: GestureDescription?) { ok = false; latch.countDown() }
            },
            null
        )
        if (!dispatched) return false
        latch.await(6, TimeUnit.SECONDS)
        return ok
    }

    fun tap(x: Float, y: Float, durationMs: Long = 60): Boolean =
        gesture(listOf(Triple(Path().apply { moveTo(x, y) }, 0L, durationMs)))

    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean =
        gesture(
            listOf(
                Triple(Path().apply { moveTo(x1, y1); lineTo(x2, y2) }, 0L, durationMs)
            )
        )

    fun pinch(cx: Float, cy: Float, fromRadius: Float, toRadius: Float, durationMs: Long): Boolean {
        val a = Path().apply { moveTo(cx - fromRadius, cy); lineTo(cx - toRadius, cy) }
        val b = Path().apply { moveTo(cx + fromRadius, cy); lineTo(cx + toRadius, cy) }
        return gesture(listOf(Triple(a, 0L, durationMs), Triple(b, 0L, durationMs)))
    }

    /** Screenshot without the MediaProjection consent dialog (API 30+). */
    fun screenshot(): ByteArray? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        return Screenshots.take(this)
    }
}
