package com.zeroui.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Intent
import android.os.SystemClock
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent

/**
 * Zero UI SOS accessibility service
 * =================================
 * Detects a triple-press SOS gesture on a hardware button while the app is
 * minimised or the screen is locked, and hands it to the same shared emergency
 * state machine that fall detection uses (via a broadcast MainActivity /
 * Dart forwards to the controller).
 *
 * Why a hardware *volume* key and not the power button
 * ----------------------------------------------------
 * Android exposes no public, policy-compliant API for an ordinary app to
 * observe the power button: it is excluded from accessibility key filtering
 * on real devices, `SCREEN_ON/OFF` broadcasts cannot cold-start a background
 * app on Android 12+, and there is no emergency-role API a normal app can
 * claim. Volume keys, however, ARE deliverable to an AccessibilityService
 * using `flagRequestFilterKeyEvents` with `onKeyEvent` — including while the
 * app is backgrounded and the screen is locked, because the service runs in
 * the system's accessibility context. The documented platform blocker for the
 * exact power-button gesture is reported in the project README; this is the
 * closest reliable supported hardware-button trigger, NOT a silent substitute.
 *
 * Gesture definition (documented, configurable):
 *   Three presses of VOLUME_DOWN within `windowMs` of the first press.
 *   Sustained long-press (which changes media volume and is how users usually
 *   adjust it) does not count — only distinct press events. A single press or
 *   the usual volume adjust cannot complete the sequence.
 *
 * Privacy scope (also in sos_accessibility_config.xml):
 *   canRetrieveWindowContent=false, no accessibility event subscriptions, no
 *   gesture injection. The service receives ONLY hardware key events.
 *
 * Hand-off: after the third press this service broadcasts
 * ACTION_SOS_TRIGGERED with the trigger source `power_button` (name preserved
 * end-to-end; see README for the historical name). The emergency foreground
 * service start happens in the app process, which is alive whenever this
 * event fires — the accessibility service itself never touches networking.
 */
class SosAccessibilityService : AccessibilityService() {

    companion object {
        const val ACTION_SOS_TRIGGERED = "com.zeroui.app.SOS_TRIGGERED"
        const val EXTRA_TRIGGER_SOURCE = "trigger_source"
        const val TRIGGER_SOURCE = "power_button"

        /** The gesture: third press must arrive within this window (ms). */
        const val TRIPLE_PRESS_WINDOW_MS = 1200L

        /** Minimum spacing so one very fast triple-fire doesn't double-trigger. */
        const val SEQUENCE_COOLDOWN_MS = 4000L

        /** Marks the service as enabled at the app layer (Settings screen read). */
        @Volatile
        var instanceActive: SosAccessibilityService? = null
            private set
    }

    private var firstPressAt = 0L
    private var pressCount = 0
    private var lastSequenceAt = 0L

    override fun onServiceConnected() {
        super.onServiceConnected()
        serviceInfo = AccessibilityServiceInfo().apply {
            eventTypes = 0 // no window/content events
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.FLAG_REQUEST_FILTER_KEY_EVENTS
            notificationTimeout = 0
        }
        instanceActive = this
    }

    override fun onDestroy() {
        if (instanceActive === this) instanceActive = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onKeyEvent(event: KeyEvent?): Boolean {
        event ?: return false
        if (event.action != KeyEvent.ACTION_DOWN) return false
        if (event.keyCode != KeyEvent.KEYCODE_VOLUME_DOWN) return false
        if (event.repeatCount > 0) return false // hold-to-decrease-volume is not a press

        val now = SystemClock.elapsedRealtime()

        // Expired sequence → this press starts a new one.
        if (firstPressAt != 0L && now - firstPressAt > TRIPLE_PRESS_WINDOW_MS) {
            pressCount = 0
            firstPressAt = 0L
        }
        // Global cooldown right after a completed gesture so the same squeeze
        // cannot re-fire while the button is still being pressed repeatedly.
        if (lastSequenceAt != 0L && now - lastSequenceAt < SEQUENCE_COOLDOWN_MS) {
            return false
        }

        if (firstPressAt == 0L) firstPressAt = now
        pressCount += 1

        if (pressCount >= 3) {
            pressCount = 0
            firstPressAt = 0L
            lastSequenceAt = now
            fireSos()
        }
        // Returning false lets the volume change still happen (honest UX: the
        // user will see/hear the volume HUD as feedback they are pressing it).
        return false
    }

    private fun fireSos() {
        val intent = Intent(ACTION_SOS_TRIGGERED).apply {
            `package` = packageName
            putExtra(EXTRA_TRIGGER_SOURCE, TRIGGER_SOURCE)
        }
        sendBroadcast(intent)
    }
}
