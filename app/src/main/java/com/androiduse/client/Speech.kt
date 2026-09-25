package com.androiduse.client

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import java.util.Locale

/**
 * Reads messages aloud, for someone who cannot easily read the screen.
 *
 * Text-to-speech initialises asynchronously, so anything asked for before it
 * is ready waits in a short queue rather than being dropped.
 */
object Speech : TextToSpeech.OnInitListener {

    private var tts: TextToSpeech? = null
    private var ready = false
    private val pending = mutableListOf<String>()
    private val main = Handler(Looper.getMainLooper())

    fun say(ctx: Context, text: String) {
        val app = ctx.applicationContext
        main.post {
            synchronized(this) {
                if (tts == null) tts = TextToSpeech(app, this)
                if (ready) speakNow(text) else pending.add(text)
            }
        }
    }

    override fun onInit(status: Int) {
        synchronized(this) {
            ready = status == TextToSpeech.SUCCESS
            if (ready) pending.forEach { speakNow(it) }
            pending.clear()
        }
    }

    private fun speakNow(text: String) {
        val engine = tts ?: return
        // The default voice is often English-only. Pick the language from the
        // script so a message in Telugu or Hindi is read in Telugu or Hindi.
        localeFor(text)?.let { locale ->
            if (engine.isLanguageAvailable(locale) >= TextToSpeech.LANG_AVAILABLE) {
                engine.language = locale
            }
        }
        engine.speak(text, TextToSpeech.QUEUE_ADD, null, "au-${System.nanoTime()}")
    }

    private fun localeFor(text: String): Locale? {
        for (ch in text) {
            when (ch.code) {
                in 0x0900..0x097F -> return Locale.forLanguageTag("hi-IN")
                in 0x0C00..0x0C7F -> return Locale.forLanguageTag("te-IN")
                in 0x0B80..0x0BFF -> return Locale.forLanguageTag("ta-IN")
                in 0x0C80..0x0CFF -> return Locale.forLanguageTag("kn-IN")
                in 0x0D00..0x0D7F -> return Locale.forLanguageTag("ml-IN")
                in 0x0980..0x09FF -> return Locale.forLanguageTag("bn-IN")
            }
        }
        return null
    }
}
