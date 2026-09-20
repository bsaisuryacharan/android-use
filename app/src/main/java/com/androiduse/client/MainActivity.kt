package com.androiduse.client

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.net.Uri
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

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
                     "connected (step 4) - you can send the code now and the " +
                     "address later.",
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

        container.addView(text("Floating chat panel", 20f, bold = true, padTop = 40))
        val canOverlay = Settings.canDrawOverlays(this)
        container.addView(
            text(
                if (canOverlay)
                    "Show the Claude chat in a small movable window on top of your " +
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

        container.addView(text("Control", 20f, bold = true, padTop = 40))
        val granted = Security.isGranted(this)
        val mins = Security.grantRemainingMs(this) / 60000
        container.addView(
            text(
                if (granted) "Your helper can control this phone for about $mins more minute(s)."
                else "Your helper cannot control this phone right now.",
                16f
            )
        )
        container.addView(button("Allow help for 8 hours") {
            Security.grantFor(this, 8 * 60); BridgeService.start(this); render()
        })
        container.addView(button("Allow help for 30 days") {
            Security.grantFor(this, 30 * 24 * 60); BridgeService.start(this); render()
        })
        container.addView(button("Stop help now") { Security.revoke(this); render() })

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
