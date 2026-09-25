package com.androiduse.client

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
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
    private const val KEY_LAST_REQUEST = "last_grant_request"

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
        "com.google.android.apps.photos",
        // Returning to the Claude app is how a task on the same phone ends.
        "com.anthropic.claude"
    )

    // Kept in step with safety.py on the server, so both sides agree on what
    // counts as a banking or payment app.
    private val SENSITIVE_HINTS = listOf(
        "bank", "wallet", "pay", "crypto", "binance", "coinbase", "phonepe", "paisa",
        "freecharge", "mobikwik", "venmo", "zelle", "revolut", "monzo", "wellsfargo",
        "barclays", "hsbc", "icici", "hdfc", "kotak", "zerodha", "groww", "upstox",
        "robinhood", "transferwise", "cashapp", "loan", "npci"
    )
    private val SENSITIVE_TOKENS = setOf(
        "upi", "sbi", "axis", "citi", "chase", "amex", "card", "cash", "bhim", "idfc",
        "cred", "trading", "stocks", "broker"
    )
    private val SENSITIVE_PREFIXES = listOf("upi", "bhim")
    private val SENSITIVE_LABEL =
        Regex("\\b(bank|banking|pay|wallet|upi|loan|credit card|crypto)\\b", RegexOption.IGNORE_CASE)

    private val RISKY = listOf(
        Regex(
            "\\b(pay|pay now|payment|make payment|buy|buy now|purchase|confirm purchase|" +
                "checkout|check out|place order|order now|confirm order|proceed to pay|" +
                "transfer|send money|donate|subscribe|top up|top-up|recharge|add money|" +
                "book now|confirm booking)\\b",
            RegexOption.IGNORE_CASE
        ),
        Regex("^(send|post|publish|forward|submit)\\b|\\b(send|post|publish|submit)$", RegexOption.IGNORE_CASE),
        Regex(
            "^((voice|video|audio) )?call(?! (log|logs|history|settings|forwarding|waiting|" +
                "barring|blocking|recording|screening))\\b|^dial\\b|\\bcall now\\b",
            RegexOption.IGNORE_CASE
        ),
        Regex(
            "\\b(delete|remove|erase|wipe|uninstall|factory reset|reset|clear data|" +
                "clear storage|empty trash|discard)\\b|^format\\b",
            RegexOption.IGNORE_CASE
        ),
        Regex(
            "\\b(sign out|log out|logout|log off|deactivate|close account|unpair|unlink|" +
                "forget network|forget this network|forget device)\\b|^forget$",
            RegexOption.IGNORE_CASE
        ),
    )
    private val RISKY_WORDS = listOf(
        "pagar", "comprar", "payer", "acheter", "bezahlen", "kaufen", "भुगतान", "पे करें",
        "చెల్లించు", "చెల్లించండి", "enviar", "envoyer", "senden", "भेजें", "भेजे", "పంపు",
        "పంపండి", "llamar", "appeler", "anrufen", "कॉल करें", "కాల్ చేయి", "eliminar",
        "borrar", "excluir", "supprimer", "löschen", "entfernen", "हटाएं", "मिटाएं",
        "తొలగించు", "తొలగించండి"
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

    /** Banking, payment and trading apps. Generous on purpose: a false
     *  positive costs one allowlist entry, a false negative an operated bank. */
    fun looksSensitive(pkg: String, label: String = ""): Boolean {
        val lower = pkg.lowercase()
        if (lower.isEmpty()) return false
        if (SENSITIVE_HINTS.any { lower.contains(it) }) return true
        val tokens = lower.split('.', '_')
        if (tokens.any { it in SENSITIVE_TOKENS }) return true
        if (tokens.any { t -> SENSITIVE_PREFIXES.any { t.startsWith(it) } }) return true
        return label.isNotEmpty() && SENSITIVE_LABEL.containsMatchIn(label)
    }

    /** A sensitive app the owner has not explicitly allowed: not to be read or driven. */
    fun isOffLimits(ctx: Context, pkg: String, label: String = ""): Boolean =
        looksSensitive(pkg, label) && !isAllowed(ctx, pkg)

    /** Does tapping something with this label send, pay, delete or call? */
    fun riskyLabel(label: String): Boolean {
        val text = label.trim()
        if (text.isEmpty()) return false
        if (RISKY.any { it.containsMatchIn(text) }) return true
        val low = text.lowercase()
        return RISKY_WORDS.any { low.contains(it.lowercase()) }
    }

    /** Apps that open any web address - so a link landing in one is just a page. */
    fun browsers(ctx: Context): Set<String> {
        val probe = Intent(Intent.ACTION_VIEW, Uri.parse("https://www.example.com/"))
            .addCategory(Intent.CATEGORY_BROWSABLE)
        @Suppress("DEPRECATION")
        return ctx.packageManager.queryIntentActivities(probe, PackageManager.MATCH_ALL)
            .map { it.activityInfo.packageName }
            .toSet()
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

    /**
     * Rate-limit control requests. Each one puts a notification in front of
     * the owner; a helper (or a misbehaving model) asking every few seconds
     * would be harassment, not help.
     */
    fun mayRequestGrant(ctx: Context): Boolean {
        val now = System.currentTimeMillis()
        val last = prefs(ctx).getLong(KEY_LAST_REQUEST, 0L)
        if (now - last < 60_000L) return false
        prefs(ctx).edit().putLong(KEY_LAST_REQUEST, now).apply()
        return true
    }
}
