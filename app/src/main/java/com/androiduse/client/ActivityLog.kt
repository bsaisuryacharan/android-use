package com.androiduse.client

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * What the helper did on this phone, kept on the phone itself.
 *
 * The server keeps its own log, but the owner should not need anyone else's
 * computer to find out what happened to their phone. The app shows the most
 * recent entries on its main screen.
 */
object ActivityLog {

    private const val PREFS = "android_use_activity"
    private const val KEY = "entries"
    private const val MAX = 60

    @Synchronized
    fun add(ctx: Context, message: String) {
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val entries = runCatching { JSONArray(prefs.getString(KEY, "[]")) }.getOrDefault(JSONArray())
        entries.put(JSONObject().put("t", System.currentTimeMillis()).put("m", message.take(160)))
        val kept = JSONArray()
        for (i in maxOf(0, entries.length() - MAX) until entries.length()) kept.put(entries.get(i))
        prefs.edit().putString(KEY, kept.toString()).apply()
    }

    /** Newest first. */
    @Synchronized
    fun recent(ctx: Context, limit: Int = 20): List<Pair<Long, String>> {
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val entries = runCatching { JSONArray(prefs.getString(KEY, "[]")) }.getOrDefault(JSONArray())
        val out = mutableListOf<Pair<Long, String>>()
        for (i in entries.length() - 1 downTo 0) {
            val e = entries.optJSONObject(i) ?: continue
            out.add(e.optLong("t") to e.optString("m"))
            if (out.size >= limit) break
        }
        return out
    }
}
