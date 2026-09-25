package com.androiduse.client

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.format.DateFormat
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.util.Date

/**
 * Setup checklist and kill switch.
 *
 * Written for someone who may be doing this alone, possibly on the phone to
 * whoever is helping them. Every step says what it is for in plain words,
 * shows whether it is done, and opens exactly the screen it needs.
 */
class MainActivity : Activity() {

    private lateinit var container: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(44, 56, 44, 72)
        }
        setContentView(ScrollView(this).apply { addView(container) })
    }

    override fun onResume() {
        super.onResume()
        BridgeService.start(this)
        render()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        BridgeService.refresh(this)
        render()
    }

    private fun render() {
        container.removeAllViews()
        val ready = Setup.allReady(this)

        container.addView(text("Android Use", 28f, bold = true))
        container.addView(
            text(
                "This lets someone you trust help you use this phone - " +
                    "reading the screen and tapping for you.",
                16f, padBottom = 36
            )
        )

        if (ready) {
            container.addView(banner("Ready to be connected", ok = true))
        } else {
            container.addView(banner("A few steps left", ok = false))
        }

        Setup.steps(this).forEachIndexed { i, step ->
            container.addView(stepView(i + 1, step))
        }

        // Pairing: the last thing, and only useful once the rest is done.
        container.addView(text("Connecting to your helper", 20f, bold = true, padTop = 40))
        val address = Setup.tailscaleAddress()
        container.addView(
            text(
                if (address.isNotBlank())
                    "Send these two things to the person helping you.\n\nAddress:  $address"
                else "Your code is ready. The address appears once Tailscale is " +
                     "connected - you can send the code now and the address later.",
                16f
            )
        )
        // The code is always available: gating it on Tailscale meant a phone
        // part-way through setup could not be paired at all.
        run {
            // Reading 64 hex characters down the phone is not realistic, so the
            // normal path is to send them. Showing them stays as a fallback.
            container.addView(button("Send these details to my helper") {
                val where = if (address.isNotBlank()) address else "(not connected yet)"
                val body = "Android Use setup\n\nAddress: $where\nCode: ${Security.token(this)}"
                startActivity(
                    Intent.createChooser(
                        Intent(Intent.ACTION_SEND)
                            .setType("text/plain")
                            .putExtra(Intent.EXTRA_SUBJECT, "Android Use setup")
                            .putExtra(Intent.EXTRA_TEXT, body),
                        "Send to your helper"
                    )
                )
            })
            container.addView(button("Show the code on screen") {
                container.addView(
                    text("Address: $address\n\nCode:\n${Security.token(this)}", 15f, mono = true)
                )
                Toast.makeText(this, "Scroll down to see it", Toast.LENGTH_LONG).show()
            })
        }

        container.addView(text("Control", 20f, bold = true, padTop = 40))
        val granted = Security.isGranted(this)
        val mins = Security.grantRemainingMs(this) / 60000
        container.addView(
            text(
                if (granted) "Your helper can control this phone for about $mins more minute(s)."
                else "Your helper cannot control this phone right now. If they ask, a " +
                    "notification lets you allow it with one tap.",
                16f
            )
        )
        container.addView(button("Allow help for 1 hour") { allow(60) })
        container.addView(button("Allow help for 8 hours") { allow(8 * 60) })
        container.addView(button("Allow help for 30 days") { allow(30 * 24 * 60) })
        container.addView(button("Stop help now") {
            Security.revoke(this)
            ControlAccessibilityService.instance?.overlays?.removeAll()
            ActivityLog.add(this, "You stopped the help")
            BridgeService.refresh(this)
            render()
        })

        container.addView(text("Safety", 20f, bold = true, padTop = 40))
        val confirm = Security.confirmSensitive(this)
        container.addView(
            text(
                if (confirm) "Before your helper sends a message, pays, deletes something or " +
                    "makes a call, this phone asks you first. (On)"
                else "Your helper can send, pay, delete and call without asking you on this " +
                    "phone first. (Off)",
                16f
            )
        )
        container.addView(button(if (confirm) "Stop asking me" else "Ask me first (recommended)") {
            Security.setConfirmSensitive(this, !confirm)
            ActivityLog.add(this, if (confirm) "You turned off 'ask me first'" else "You turned on 'ask me first'")
            render()
        })

        val allowed = Security.allowedPackages(this)
        container.addView(
            text(
                "Your helper can open ${allowed.size} app(s) on this phone. Banking and " +
                    "payment apps stay off-limits unless you tick them here.",
                16f, padTop = 16
            )
        )
        container.addView(button("Choose which apps they can open") { chooseApps() })

        container.addView(text("Extra permissions (optional)", 20f, bold = true, padTop = 40))
        container.addView(
            text(
                "These let your helper fix common problems directly instead of " +
                    "hunting through settings screens.",
                15f
            )
        )
        val canWrite = DeviceControl.canWriteSystem(this)
        container.addView(
            text(
                (if (canWrite) "✓ " else "") + "Change screen brightness, text size and " +
                    "how soon the screen turns off.",
                16f, padTop = 16
            )
        )
        if (!canWrite) {
            container.addView(button("Allow") {
                startActivity(
                    Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:$packageName"))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            })
        }
        val canDnd = DeviceControl.canControlDnd(this)
        container.addView(
            text((if (canDnd) "✓ " else "") + "Turn Do Not Disturb on and off.", 16f, padTop = 16)
        )
        if (!canDnd) {
            container.addView(button("Allow") {
                startActivity(
                    Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            })
        }

        container.addView(text("Using Claude on this same phone", 20f, bold = true, padTop = 40))
        container.addView(
            text(
                "Open Claude in split screen or a pop-up window next to the app you " +
                    "want help with. Android Use reads the other app, not the chat. You " +
                    "can also just ask Claude - it will switch apps and come back.",
                16f
            )
        )

        container.addView(text("Floating chat panel", 20f, bold = true, padTop = 40))
        val canOverlay = Settings.canDrawOverlays(this)
        container.addView(
            text(
                if (canOverlay)
                    "Show the Claude website in a small movable window on top of your " +
                    "other apps - so you can see the chat while Claude works."
                else "To float the chat over other apps, allow \"Display over " +
                    "other apps\" first.",
                16f
            )
        )
        if (!canOverlay) {
            container.addView(button("Allow display over other apps") {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName")
                    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            })
        } else {
            container.addView(button("Show floating Claude panel") {
                startService(Intent(this, OverlayService::class.java))
                moveTaskToBack(true)  // step aside so the panel is over your apps
            })
            container.addView(button("Hide floating panel") {
                startService(
                    Intent(this, OverlayService::class.java)
                        .setAction(OverlayService.ACTION_STOP)
                )
            })
        }

        container.addView(text("What your helper did recently", 20f, bold = true, padTop = 40))
        val recent = ActivityLog.recent(this, 12)
        if (recent.isEmpty()) {
            container.addView(text("Nothing yet.", 15f))
        } else {
            val timeFormat = DateFormat.getTimeFormat(this)
            val dateFormat = DateFormat.getDateFormat(this)
            val today = dateFormat.format(Date())
            recent.forEach { (at, message) ->
                val day = dateFormat.format(Date(at))
                val stamp = if (day == today) timeFormat.format(Date(at)) else "$day ${timeFormat.format(Date(at))}"
                container.addView(text("$stamp  —  $message", 14f, padTop = 6))
            }
        }

        if (!Setup.canSelfRepair(this)) {
            container.addView(
                text(
                    "Note for your helper: this phone was set up without a cable, " +
                        "so it cannot switch its own accessibility setting back on " +
                        "if Android turns it off. If help stops working, open this " +
                        "app and check step 1.",
                    13f, padTop = 28
                )
            )
        }
    }

    /**
     * The on-device allowlist, edited by the owner. The server has its own
     * list too, but this one is the one that counts: it is enforced here, on
     * the phone, whatever the server says.
     */
    private fun chooseApps() {
        val pm = packageManager
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        @Suppress("DEPRECATION")
        val apps = pm.queryIntentActivities(launcher, 0)
            .map { it.activityInfo.packageName to it.loadLabel(pm).toString() }
            .filter { (pkg, _) -> pkg != packageName }
            .distinctBy { it.first }
            .sortedBy { it.second.lowercase() }
        val allowed = Security.allowedPackages(this).toMutableSet()
        val labels = apps.map { (pkg, label) ->
            if (Security.looksSensitive(pkg, label)) "⚠ $label (banking or payment)" else label
        }.toTypedArray()
        val checked = apps.map { it.first in allowed }.toBooleanArray()
        android.app.AlertDialog.Builder(this)
            .setTitle("Apps your helper can open")
            .setMultiChoiceItems(labels, checked) { _, which, isChecked ->
                if (isChecked) allowed.add(apps[which].first) else allowed.remove(apps[which].first)
            }
            .setPositiveButton("Save") { _, _ ->
                Security.setAllowedPackages(this, allowed)
                ActivityLog.add(this, "You changed which apps your helper can open (${allowed.size})")
                render()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun describe(minutes: Int): String = when {
        minutes < 60 -> "$minutes minutes"
        minutes < 24 * 60 -> "${minutes / 60} hour${if (minutes < 120) "" else "s"}"
        else -> "${minutes / (24 * 60)} days"
    }

    private fun allow(minutes: Int) {
        Security.grantFor(this, minutes)
        ActivityLog.add(this, "You allowed help for ${describe(minutes)}")
        BridgeService.start(this)
        BridgeService.refresh(this)
        render()
    }

    // ----------------------------------------------------------------- views

    private fun stepView(number: Int, step: Setup.Step): ViewGroup {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 28, 28, 28)
            setBackgroundColor(if (step.done) 0xFF14351B.toInt() else 0xFF2A2A2A.toInt())
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = 22 }
        }
        val mark = if (step.done) "✓" else "$number."
        box.addView(text("$mark  ${step.title}", 19f, bold = true))
        box.addView(text(step.why, 15f, padTop = 8))
        if (!step.done) {
            if (step.hint.isNotBlank()) box.addView(text(step.hint, 14f, padTop = 12))
            box.addView(button(step.actionLabel) { step.action(this); })
            if (step.hint.contains("App info")) {
                box.addView(button("App info") { Setup.openAppInfo(this) })
            }
            // This phone may not let the app read the always-on VPN setting,
            // so let the person say they have done it rather than stranding
            // them on a step that never completes.
            if (step.title.contains("after a restart")) {
                box.addView(button("I have turned it on") {
                    Setup.acknowledgeAlwaysOn(this); render()
                })
            }
        }
        return box
    }

    private fun banner(message: String, ok: Boolean) = TextView(this).apply {
        text = message
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(Color.WHITE)
        gravity = Gravity.CENTER
        setPadding(24, 26, 24, 26)
        setBackgroundColor(if (ok) 0xFF1B5E20.toInt() else 0xFF5D4037.toInt())
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { bottomMargin = 30 }
    }

    private fun text(
        value: String, size: Float, bold: Boolean = false,
        padTop: Int = 0, padBottom: Int = 0, mono: Boolean = false,
    ) = TextView(this).apply {
        text = value
        setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
        if (bold) setTypeface(typeface, Typeface.BOLD)
        if (mono) setTypeface(Typeface.MONOSPACE)
        setTextIsSelectable(mono)
        setPadding(0, padTop, 0, padBottom)
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = 16 }
        setOnClickListener { onClick() }
    }
}
