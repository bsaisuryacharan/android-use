package com.androiduse.client

import android.app.Activity
import android.app.KeyguardManager
import android.os.Bundle
import android.view.WindowManager

/**
 * Turns the screen on, invisibly.
 *
 * Phones lock themselves after a minute of no touches - which is exactly what
 * happens while an assistant is thinking between steps. An app cannot press
 * the power button, but an activity allowed to show over the lock screen can
 * turn the screen on. A swipe lock is dismissed; a PIN, pattern or fingerprint
 * lock is not bypassed - Android shows its unlock screen for the owner.
 *
 * The activity is transparent and finishes itself as soon as its job is done.
 */
class WakeActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        // Some OEM builds still only honour the old window flags.
        @Suppress("DEPRECATION")
        window.addFlags(
            WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
        )

        val keyguard = getSystemService(KeyguardManager::class.java)
        if (keyguard != null && keyguard.isKeyguardLocked) {
            keyguard.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() = finishSoon(150)
                override fun onDismissCancelled() = finishSoon(150)
                override fun onDismissError() = finishSoon(150)
            })
            // If nobody enters the PIN, do not sit on top of the lock screen.
            finishSoon(30_000)
        } else {
            finishSoon(400)
        }
    }

    private fun finishSoon(delayMs: Long) {
        window.decorView.postDelayed({ if (!isFinishing) finish() }, delayMs)
    }
}
