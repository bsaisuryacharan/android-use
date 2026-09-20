package com.androiduse.client

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.graphics.ColorSpace
import android.hardware.HardwareBuffer
import android.os.Build
import android.view.Display
import androidx.annotation.RequiresApi
import java.io.ByteArrayOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Screenshots straight from the accessibility service.
 *
 * On API 30+ this needs no MediaProjection consent dialog, which is what makes
 * unattended help possible - the user is not asked to approve a capture every
 * time the assistant needs to look at the screen.
 */
object Screenshots {

    /** Android rate-limits accessibility screenshots; a burst gets rejected. */
    private const val RETRY_DELAY_MS = 1200L

    @RequiresApi(Build.VERSION_CODES.R)
    fun take(service: AccessibilityService, quality: Int = 80): ByteArray? {
        capture(service, quality)?.let { return it }
        // A second call too soon fails with ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT.
        // Waiting out the interval turns a hard failure into a slight delay.
        Thread.sleep(RETRY_DELAY_MS)
        return capture(service, quality)
    }

    @Volatile
    var lastError: Int = 0
        private set

    @RequiresApi(Build.VERSION_CODES.R)
    private fun capture(service: AccessibilityService, quality: Int): ByteArray? {
        val latch = CountDownLatch(1)
        var result: ByteArray? = null
        val executor = Executors.newSingleThreadExecutor()
        try {
            service.takeScreenshot(
                Display.DEFAULT_DISPLAY,
                executor,
                object : AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(screenshot: AccessibilityService.ScreenshotResult) {
                        var buffer: HardwareBuffer? = null
                        try {
                            buffer = screenshot.hardwareBuffer
                            val cs = screenshot.colorSpace
                                ?: ColorSpace.get(ColorSpace.Named.SRGB)
                            val bitmap = Bitmap.wrapHardwareBuffer(buffer, cs)
                            if (bitmap != null) {
                                // Downscale here: sending a full 1080x2400 frame over a
                                // phone's uplink is slow and the detail is not needed.
                                val scaled = scale(bitmap, 800)
                                val out = ByteArrayOutputStream()
                                scaled.compress(Bitmap.CompressFormat.JPEG, quality, out)
                                result = out.toByteArray()
                            }
                        } catch (_: Throwable) {
                        } finally {
                            runCatching { buffer?.close() }
                            latch.countDown()
                        }
                    }

                    override fun onFailure(errorCode: Int) {
                        lastError = errorCode
                        latch.countDown()
                    }
                }
            )
            latch.await(10, TimeUnit.SECONDS)
        } catch (_: Throwable) {
            return null
        } finally {
            executor.shutdown()
        }
        return result
    }

    private fun scale(source: Bitmap, maxWidth: Int): Bitmap {
        if (source.width <= maxWidth) {
            return source.copy(Bitmap.Config.ARGB_8888, false) ?: source
        }
        val ratio = maxWidth.toFloat() / source.width
        val height = (source.height * ratio).toInt()
        val software = source.copy(Bitmap.Config.ARGB_8888, false) ?: source
        val scaled = Bitmap.createScaledBitmap(software, maxWidth, height, true)
        if (software !== scaled && software !== source) software.recycle()
        return scaled
    }
}
