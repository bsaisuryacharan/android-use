package com.androiduse.client

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.app.KeyguardManager
import android.app.Notification
import android.content.Context
import android.graphics.Path
import android.graphics.Rect
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.os.SystemClock
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityNodeInfo.AccessibilityAction
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

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
        const val CLAUDE_PACKAGE = "com.anthropic.claude"
        private const val TOAST_TTL_MS = 6_000L
    }

    /** Guards `indexed`: the bridge serves requests on several threads, and a
     *  read replacing the list while an action uses it would act on garbage. */
    private val lock = Any()

    /** Nodes from the last read, so the bridge can act on them by index. */
    private val indexed = mutableListOf<AccessibilityNodeInfo>()
    private var mainScroller: AccessibilityNodeInfo? = null

    lateinit var overlays: Overlays
        private set

    @Volatile
    private var lastChange = 0L
    private val toasts = ArrayDeque<Pair<Long, String>>()
    private val appLabels = HashMap<String, String>()

    override fun onServiceConnected() {
        super.onServiceConnected()
        overlays = Overlays(this)
        instance = this
        BridgeService.start(this)
    }

    override fun onDestroy() {
        instance = null
        if (::overlays.isInitialized) overlays.removeAll()
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        event ?: return
        if (event.packageName?.toString() == packageName) return  // our own overlays
        if (event.eventType == AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED) {
            // A toast arrives as a notification event with no Notification
            // attached. It is often the only confirmation an action worked
            // ("Message sent"), and it is gone in two seconds.
            if (event.parcelableData !is Notification) {
                val text = event.text.joinToString(" ").trim()
                if (text.isNotEmpty()) synchronized(toasts) {
                    toasts.addLast(SystemClock.uptimeMillis() to text.take(200))
                    while (toasts.size > 5) toasts.removeFirst()
                }
            }
            return
        }
        lastChange = SystemClock.uptimeMillis()
    }

    override fun onInterrupt() {}

    /**
     * Wait until the screen stops changing, or give up. A read right after a
     * tap otherwise catches a frame of animation - half of the old screen and
     * half of the new.
     */
    fun awaitIdle(quietMs: Long = 350, maxMs: Long = 2_500) {
        val start = SystemClock.uptimeMillis()
        Thread.sleep(120)  // let the events the last action caused arrive
        while (SystemClock.uptimeMillis() - start < maxMs) {
            if (SystemClock.uptimeMillis() - lastChange >= quietMs) return
            Thread.sleep(50)
        }
    }

    private fun recentToasts(): JSONArray {
        val now = SystemClock.uptimeMillis()
        val out = JSONArray()
        synchronized(toasts) {
            toasts.filter { now - it.first < TOAST_TTL_MS }.forEach { out.put(it.second) }
        }
        return out
    }

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

    fun appLabel(pkg: String): String {
        if (pkg.isEmpty()) return ""
        synchronized(appLabels) { appLabels[pkg]?.let { return it } }
        val label = runCatching {
            packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
        }.getOrDefault("")
        synchronized(appLabels) { appLabels[pkg] = label }
        return label
    }

    /**
     * The window to read, and the package of any window deliberately skipped.
     *
     * Normally the active window. But when our own panel is in front, or the
     * Claude chat shares the screen (split screen, pop-up view), Claude must
     * read the app being worked on, not itself - so if another app window is
     * visible, read the largest one of those instead.
     */
    private fun windowToRead(): Pair<AccessibilityNodeInfo?, String> {
        val active = rootInActiveWindow
        val activePkg = active?.packageName?.toString()
        if (active != null && activePkg != packageName && activePkg != CLAUDE_PACKAGE) {
            return active to ""
        }
        var best: AccessibilityNodeInfo? = null
        var bestArea = -1
        for (w in windows) {
            if (w.type != AccessibilityWindowInfo.TYPE_APPLICATION) continue
            val node = w.root ?: continue
            val pkg = node.packageName?.toString()
            if (pkg == packageName || pkg == CLAUDE_PACKAGE) continue
            val b = Rect().also { w.getBoundsInScreen(it) }
            val area = b.width() * b.height()
            if (area > bestArea) {
                bestArea = area
                best = node
            }
        }
        if (best != null) return best to (if (activePkg == CLAUDE_PACKAGE) CLAUDE_PACKAGE else "")
        // Nothing else is on screen: the chat (or our own app) is what there is.
        return active to ""
    }

    /** The app in front and its name - what the sensitive-app guard judges. */
    fun foreground(): Pair<String, String> {
        val pkg = windowToRead().first?.packageName?.toString().orEmpty()
        return pkg to appLabel(pkg)
    }

    private fun box(r: Rect) = JSONArray(listOf(r.left, r.top, r.right, r.bottom))

    fun readScreen(maxTexts: Int = 15): JSONObject {
        val (root, skipped) = windowToRead()
        root ?: return JSONObject().put("error", "No window is readable right now.")

        val elements = JSONArray()
        val looseText = JSONArray()
        var scrollRegion: Rect? = null

        synchronized(lock) {
            indexed.forEach { runCatching { @Suppress("DEPRECATION") it.recycle() } }
            indexed.clear()
            mainScroller = null
            var scrollerArea = -1

            fun visit(node: AccessibilityNodeInfo?, insideActionable: Boolean, container: Rect?) {
                if (node == null || elements.length() >= MAX_ELEMENTS) return
                val bounds = Rect().also { node.getBoundsInScreen(it) }
                val onScreen = bounds.width() > 0 && bounds.height() > 0
                var childContainer = container
                if (node.isScrollable && onScreen) {
                    val area = bounds.width() * bounds.height()
                    if (area > scrollerArea) {
                        scrollerArea = area
                        scrollRegion = Rect(bounds)
                        @Suppress("DEPRECATION")
                        mainScroller = AccessibilityNodeInfo.obtain(node)
                    }
                    childContainer = Rect(bounds)
                }
                // Skip our own floating panel; Claude drives the app under it.
                if (node.packageName == packageName && OverlayService.showing) {
                    for (i in 0 until node.childCount) visit(node.getChild(i), insideActionable, childContainer)
                    return
                }
                var nowInside = insideActionable
                if (onScreen && node.isVisibleToUser && isActionable(node)) {
                    val (checkable, checked) = checkableState(node)
                    var label = labelOf(node)
                    // An empty field reports its placeholder as its text.
                    if (node.isEditable && node.isShowingHintText) label = ""
                    if (node.isPassword && label.isNotEmpty()) label = "•".repeat(label.length.coerceAtMost(12))
                    val hint = node.hintText?.toString()?.trim().orEmpty()
                    elements.put(
                        JSONObject()
                            .put("index", indexed.size)
                            .put("label", label)
                            .put("cls", node.className?.toString()?.substringAfterLast('.') ?: "item")
                            .put("resource_id", node.viewIdResourceName ?: "")
                            .put("editable", node.isEditable)
                            .put("scrollable", node.isScrollable)
                            .put("enabled", node.isEnabled)
                            .put("checkable", checkable)
                            .put("checked", checked)
                            .put("bounds", box(bounds))
                            .put("focused", node.isFocused)
                            .put("selected", node.isSelected)
                            .put("password", node.isPassword)
                            .put("hint", if (node.isEditable || label.isEmpty()) hint else "")
                            .put("long_clickable", node.isLongClickable)
                            .put("container", container?.let { box(it) } ?: JSONObject.NULL)
                    )
                    @Suppress("DEPRECATION")
                    indexed.add(AccessibilityNodeInfo.obtain(node))
                    nowInside = true
                } else if (!insideActionable && onScreen && node.isVisibleToUser) {
                    node.text?.toString()?.trim()?.takeIf { it.isNotEmpty() && it.length < 400 }
                        ?.let { if (looseText.length() < maxTexts) looseText.put(it) }
                }
                for (i in 0 until node.childCount) visit(node.getChild(i), nowInside, childContainer)
            }
            visit(root, false, null)
        }

        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        val keyguard = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        val size = (getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager)
            .maximumWindowMetrics.bounds
        val pkg = root.packageName?.toString() ?: ""
        return JSONObject()
            .put("protocol", HttpBridge.PROTOCOL)
            .put("package", pkg)
            .put("app_label", appLabel(pkg))
            .put("activity", "")
            .put("elements", elements)
            .put("texts", looseText)
            .put("width", size.width())
            .put("height", size.height())
            .put("scroll_region", scrollRegion?.let { box(it) } ?: JSONObject.NULL)
            .put("screen_on", power.isInteractive)
            .put("locked", keyguard.isKeyguardLocked)
            .put("keyboard_open", windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD })
            .put("toasts", recentToasts())
            .put("other_window", skipped)
    }

    // ---------------------------------------------------------------- acting

    /** A private copy of an indexed node, safe to use outside the lock. */
    private fun nodeCopy(index: Int): AccessibilityNodeInfo? = synchronized(lock) {
        @Suppress("DEPRECATION")
        indexed.getOrNull(index)?.let { AccessibilityNodeInfo.obtain(it) }
    }

    private fun centerOf(node: AccessibilityNodeInfo): Pair<Float, Float> {
        val b = Rect().also { node.getBoundsInScreen(it) }
        return b.exactCenterX() to b.exactCenterY()
    }

    fun labelAt(index: Int): String? = nodeCopy(index)?.let { labelOf(it) }

    /** The label of the smallest element under a point, to judge a coordinate tap. */
    fun labelAtPoint(x: Float, y: Float): String? = synchronized(lock) {
        indexed
            .map { it to Rect().also { r -> it.getBoundsInScreen(r) } }
            .filter { (_, r) -> r.contains(x.toInt(), y.toInt()) }
            .minByOrNull { (_, r) -> r.width() * r.height() }
            ?.let { (node, _) -> labelOf(node) }
    }

    fun centerAt(index: Int): Pair<Float, Float>? = nodeCopy(index)?.let { centerOf(it) }

    /** Click a node directly, walking up to a clickable parent if needed. */
    fun clickIndex(index: Int): Boolean {
        var node = nodeCopy(index) ?: return false
        centerOf(node).let { (x, y) -> overlays.ripple(x, y) }
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

    fun longClickIndex(index: Int): Boolean {
        val node = nodeCopy(index) ?: return false
        val (x, y) = centerOf(node)
        if (node.isLongClickable && node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)) {
            overlays.ripple(x, y)
            return true
        }
        return tap(x, y, 700)
    }

    /**
     * Type any script or emoji - the thing `adb shell input text` cannot do.
     *
     * ACTION_SET_TEXT replaces the whole field, so appending means reading
     * what is there first. `submit` presses the keyboard's enter/search key,
     * which is how a search box without a visible button is submitted.
     */
    fun setTextAt(index: Int, text: String, clear: Boolean, submit: Boolean): JSONObject {
        val node = nodeCopy(index) ?: return JSONObject().put("ok", false)
        node.refresh()
        val current = if (node.isShowingHintText) "" else node.text?.toString().orEmpty()
        // A password field reports dots, not its contents, so it can only be replaced.
        val newText = if (clear || node.isPassword) text else current + text
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, newText)
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (ok) {
            // Leave the cursor at the end, where a person would expect it.
            val end = Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, newText.length)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, newText.length)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, end)
        }
        val result = JSONObject().put("ok", ok).put("password", node.isPassword)
        if (submit && ok) result.put("submitted", imeEnter(node))
        return result
    }

    private fun imeEnter(node: AccessibilityNodeInfo): Boolean =
        node.performAction(AccessibilityAction.ACTION_IME_ENTER.id)

    private fun focusedField(): AccessibilityNodeInfo? = findFocus(AccessibilityNodeInfo.FOCUS_INPUT)

    private fun deleteLastChar(): Boolean {
        val node = focusedField() ?: return false
        val current = if (node.isShowingHintText) "" else node.text?.toString().orEmpty()
        if (current.isEmpty()) return true
        // Drop one whole character, not half of an emoji's surrogate pair.
        val cut = current.offsetByCodePoints(current.length, -1)
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, current.substring(0, cut))
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    private fun scrollableAncestor(start: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var node: AccessibilityNodeInfo? = start
        var hops = 0
        while (node != null && hops < 12) {
            if (node.isScrollable) return node
            node = node.parent
            hops++
        }
        return null
    }

    /**
     * Scroll a list with the accessibility action for it, rather than a swipe:
     * a swipe can land as a tap on whatever is under the finger, or trigger
     * pull-to-refresh at the top of a feed. Returns false when the list offers
     * no suitable action, and the server then falls back to a swipe.
     */
    fun scroll(index: Int, direction: String): Boolean {
        val target = (
            if (index >= 0) {
                nodeCopy(index)?.let { scrollableAncestor(it) }
            } else synchronized(lock) {
                @Suppress("DEPRECATION")
                mainScroller?.let { AccessibilityNodeInfo.obtain(it) }
            }
        ) ?: return false

        val directional = when (direction) {
            "down" -> AccessibilityAction.ACTION_SCROLL_DOWN
            "up" -> AccessibilityAction.ACTION_SCROLL_UP
            "left" -> AccessibilityAction.ACTION_SCROLL_LEFT
            "right" -> AccessibilityAction.ACTION_SCROLL_RIGHT
            else -> return false
        }
        if (target.actionList.any { it.id == directional.id } && target.performAction(directional.id)) {
            return true
        }
        // Forward/backward only say "further along the list", not which way
        // the list runs - so use them only when the orientation matches.
        val vertical = direction == "up" || direction == "down"
        val info = target.collectionInfo
        val cls = target.className?.toString().orEmpty()
        val horizontal = (info != null && info.rowCount <= 1 && info.columnCount > 1) ||
            cls.contains("Horizontal") || cls.contains("ViewPager") || cls.contains("Pager")
        if (vertical == horizontal) return false
        val forward = direction == "down" || direction == "right"
        return target.performAction(
            if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
        )
    }

    fun globalAction(name: String): Boolean {
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        fun media(code: Int): Boolean {
            audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
            audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
            return true
        }
        fun volume(direction: Int): Boolean {
            audio.adjustSuggestedStreamVolume(
                direction, AudioManager.USE_DEFAULT_STREAM_TYPE, AudioManager.FLAG_SHOW_UI
            )
            return true
        }
        return when (name.lowercase()) {
            "back" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "recents" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "notifications" -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
            "quick_settings" -> performGlobalAction(GLOBAL_ACTION_QUICK_SETTINGS)
            "lock" -> performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN)
            "power_menu" -> performGlobalAction(GLOBAL_ACTION_POWER_DIALOG)
            "screenshot" -> performGlobalAction(GLOBAL_ACTION_TAKE_SCREENSHOT)
            "split_screen" -> performGlobalAction(GLOBAL_ACTION_TOGGLE_SPLIT_SCREEN)
            "all_apps" -> Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                performGlobalAction(GLOBAL_ACTION_ACCESSIBILITY_ALL_APPS)
            "close_panels" -> Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                performGlobalAction(GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE)
            "enter" -> focusedField()?.let { imeEnter(it) } ?: false
            "delete" -> deleteLastChar()
            "volume_up" -> volume(AudioManager.ADJUST_RAISE)
            "volume_down" -> volume(AudioManager.ADJUST_LOWER)
            "mute" -> volume(AudioManager.ADJUST_TOGGLE_MUTE)
            "play_pause" -> media(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE)
            "next" -> media(KeyEvent.KEYCODE_MEDIA_NEXT)
            "previous" -> media(KeyEvent.KEYCODE_MEDIA_PREVIOUS)
            else -> false
        }
    }

    // --------------------------------------------------------------- gestures

    /** Dispatch a gesture and wait for it to finish, with the overlays out of the way. */
    private fun dispatchAndWait(gesture: GestureDescription): Boolean = overlays.passThrough {
        val latch = CountDownLatch(1)
        val ok = AtomicBoolean(false)
        val sent = dispatchGesture(
            gesture,
            object : GestureResultCallback() {
                override fun onCompleted(d: GestureDescription?) { ok.set(true); latch.countDown() }
                override fun onCancelled(d: GestureDescription?) { latch.countDown() }
            },
            null
        )
        if (sent) latch.await(8, TimeUnit.SECONDS)
        sent && ok.get()
    }

    private fun stroke(path: Path, start: Long, duration: Long) =
        GestureDescription.StrokeDescription(path, start, duration.coerceAtLeast(1))

    /** Gestures ADB could not do: long press, pinch, drag, multi-touch. */
    fun gesture(strokes: List<Triple<Path, Long, Long>>): Boolean {
        val builder = GestureDescription.Builder()
        strokes.forEach { (path, start, duration) -> builder.addStroke(stroke(path, start, duration)) }
        return dispatchAndWait(builder.build())
    }

    fun tap(x: Float, y: Float, durationMs: Long = 60): Boolean {
        overlays.ripple(x, y)
        return gesture(listOf(Triple(Path().apply { moveTo(x, y) }, 0L, durationMs)))
    }

    fun doubleTap(x: Float, y: Float): Boolean {
        overlays.ripple(x, y)
        val p = Path().apply { moveTo(x, y) }
        return gesture(listOf(Triple(p, 0L, 40L), Triple(Path(p), 140L, 40L)))
    }

    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean =
        gesture(listOf(Triple(Path().apply { moveTo(x1, y1); lineTo(x2, y2) }, 0L, durationMs)))

    /**
     * Press, hold, then move - what launchers and reorderable lists wait for
     * before they let something be dragged. One stroke cannot pause, so it is
     * a held stroke continued into a moving one.
     */
    fun drag(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        overlays.ripple(x1, y1)
        val hold = GestureDescription.StrokeDescription(
            Path().apply { moveTo(x1, y1); lineTo(x1 + 1f, y1 + 1f) }, 0L, 650L, true
        )
        if (!dispatchAndWait(GestureDescription.Builder().addStroke(hold).build())) return false
        val move = hold.continueStroke(
            Path().apply { moveTo(x1 + 1f, y1 + 1f); lineTo(x2, y2) }, 0L, durationMs.coerceAtLeast(300L), false
        )
        return dispatchAndWait(GestureDescription.Builder().addStroke(move).build())
    }

    fun pinch(cx: Float, cy: Float, fromRadius: Float, toRadius: Float, durationMs: Long): Boolean {
        val a = Path().apply { moveTo(cx - fromRadius, cy); lineTo(cx - toRadius, cy) }
        val b = Path().apply { moveTo(cx + fromRadius, cy); lineTo(cx + toRadius, cy) }
        return gesture(listOf(Triple(a, 0L, durationMs), Triple(b, 0L, durationMs)))
    }

    /** Screenshot without the MediaProjection consent dialog (API 30+). */
    fun screenshot(maxWidth: Int = 800): ByteArray? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        return overlays.hiddenForCapture { Screenshots.take(this, maxWidth) }
    }
}
