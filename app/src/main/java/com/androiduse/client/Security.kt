package com.androiduse.client

import android.content.Context
import android.content.SharedPreferences
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * Who may drive this phone, and what they may drive.
 *
 * The bridge accepts commands from anything that can reach the port, so the
 * token is the only thing standing between a stranger on the network and full
 * control of the device. Everything here is deliberately conservative.
 */
object Security {

    private const val PREFS = "android_use_security"
    private const val KEY_TOKEN = "token"
    private const val KEY_ALLOWED = "allowed_packages"
    private const val KEY_GRANT_UNTIL = "grant_until"
    private const val KEY_CONFIRM = "confirm_sensitive"

    /** Apps that may be launched remotely unless the owner widens it. */
    private val DEFAULT_ALLOWED = setOf(
        "com.android.settings",
        "com.android.chrome",
        "com.google.android.youtube",
        "com.android.deskclock",
        // Pixel and stock Android ship the clock under a different package
        // than most OEMs; listing only one silently blocks the other.
        "com.google.android.deskclock",
        "com.google.android.contacts",
        "com.google.android.dialer",
        "com.google.android.calculator",
        "com.google.android.calendar",
        "com.google.android.apps.maps",
        "com.google.android.apps.photos"
    )

    /** Package name fragments that should always need a human to say yes. */
    private val SENSITIVE_HINTS = listOf(
        "bank", "upi", "pay", "wallet", "binance", "crypto", "icici", "hdfc",
        "axis", "sbi", "paytm", "phonepe", "gpay", "amex", "card", "loan"
    )

    private fun prefs(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun token(ctx: Context): String {
        val existing = prefs(ctx).getString(KEY_TOKEN, null)
        if (!existing.isNullOrBlank()) return existing
        val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val generated = bytes.joinToString("") { "%02x".format(it) }
        prefs(ctx).edit().putString(KEY_TOKEN, generated).apply()
        return generated
    }

    fun rotateToken(ctx: Context): String {
        prefs(ctx).edit().remove(KEY_TOKEN).apply()
        return token(ctx)
    }

    /** Constant-time compare so the token cannot be guessed a byte at a time. */
    fun tokenMatches(ctx: Context, presented: String?): Boolean {
        if (presented.isNullOrBlank()) return false
        val a = MessageDigest.getInstance("SHA-256").digest(token(ctx).toByteArray())
        val b = MessageDigest.getInstance("SHA-256").digest(presented.toByteArray())
        return MessageDigest.isEqual(a, b)
    }

    fun allowedPackages(ctx: Context): Set<String> =
        prefs(ctx).getStringSet(KEY_ALLOWED, null) ?: DEFAULT_ALLOWED

    fun setAllowedPackages(ctx: Context, packages: Set<String>) {
        prefs(ctx).edit().putStringSet(KEY_ALLOWED, packages).apply()
    }

    fun isAllowed(ctx: Context, pkg: String): Boolean = allowedPackages(ctx).contains(pkg)

    fun looksSensitive(pkg: String): Boolean {
        val lower = pkg.lowercase()
        return SENSITIVE_HINTS.any { lower.contains(it) }
    }

    /** Control is time-boxed: an expired grant means the bridge refuses everything. */
    fun grantFor(ctx: Context, minutes: Int) {
        val until = System.currentTimeMillis() + minutes * 60_000L
        prefs(ctx).edit().putLong(KEY_GRANT_UNTIL, until).apply()
    }

    fun revoke(ctx: Context) {
        prefs(ctx).edit().putLong(KEY_GRANT_UNTIL, 0L).apply()
    }

    fun grantRemainingMs(ctx: Context): Long =
        (prefs(ctx).getLong(KEY_GRANT_UNTIL, 0L) - System.currentTimeMillis()).coerceAtLeast(0L)

    fun isGranted(ctx: Context): Boolean = grantRemainingMs(ctx) > 0

    fun confirmSensitive(ctx: Context): Boolean = prefs(ctx).getBoolean(KEY_CONFIRM, true)

    fun setConfirmSensitive(ctx: Context, value: Boolean) {
        prefs(ctx).edit().putBoolean(KEY_CONFIRM, value).apply()
    }
}
