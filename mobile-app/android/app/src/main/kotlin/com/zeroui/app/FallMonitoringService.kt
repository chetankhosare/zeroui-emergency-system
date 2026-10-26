package com.zeroui.app

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.math.sqrt

/**
 * Zero UI passive fall-monitoring foreground service
 * ==================================================
 * Why this exists
 * ---------------
 * The Dart `FallDetector` (lib/sensors.dart) is the authoritative algorithm and
 * keeps running whenever the app's process is scheduled. But when the app is
 * minimised or the screen is locked Android eventually freezes unscheduled
 * processes, so a Dart-only sensor subscription silently stops monitoring even
 * though the UI still says "monitoring for falls".
 *
 * This service fixes that with a small, *independent* native copy of the same
 * three-phase rule (free fall → impact → stillness). It owns the accelerometer
 * on a dedicated HandlerThread, keeps its own process alive with a foreground
 * service notification, and:
 *
 *   1. reports every *confirmed* fall back to Dart via a broadcast, so the
 *      app wakes up (even from a locked screen) and starts the real
 *      state machine — countdown → PIN → backend. One detection produces
 *      exactly one callback;
 *   2. while a fall is unresolved it re-broadcasts every 8 s so Dart can
 *      recover the countdown after a process death;
 *   3. exposes generic "monitoring/healthy/unavailable" reads, so the UI can
 *      show the actual state instead of assuming.
 *
 * The Kotlin thresholds mirror the Dart defaults (and stay in sync with the
 * user's settings via the `updateSettings` action). If both pipelines ever run
 * simultaneously (foreground app + service), Dart owns the countdown and
 * consumes the broadcast via `phase == passive`, so no double countdown can
 * start (`startCountdown` is guarded).
 *
 * Limits (honest): a user force-stop or a vendor "battery killer" still ends
 * monitoring until the user re-opens the app. This is documented, not denied.
 */
class FallMonitoringService : Service(), SensorEventListener {

    companion object {
        const val CHANNEL_ID = "zeroui_monitoring"
        private const val NOTIFICATION_ID = 4712

        const val ACTION_START = "com.zeroui.app.action.MONITOR_START"
        const val ACTION_STOP = "com.zeroui.app.action.MONITOR_STOP"
        const val ACTION_UPDATE_SETTINGS = "com.zeroui.app.action.MONITOR_SETTINGS"

        /** Dart → service: fall suspected (cancelled or unhandled). */
        const val EXTRA_FALL_CANCELLED = "fall_cancelled"

        /** Kotlin → Dart broadcasts. */
        const val ACTION_FALL_DETECTED = "com.zeroui.app.FALL_DETECTED"
        const val ACTION_STATE = "com.zeroui.app.MONITOR_STATE"
        const val EXTRA_IMPACT = "impact"
        const val EXTRA_FREEFALL_MS = "freefall_ms"
        const val EXTRA_STILLNESS_MS = "stillness_ms"
        const val EXTRA_STDEV = "stdev"
        const val EXTRA_STATE = "state"
        const val EXTRA_UNRESOLVED = "unresolved"

        /** Tunables — Dart defaults; refreshed via ACTION_UPDATE_SETTINGS. */
        @Volatile var freeFallThreshold = 4.0f
        @Volatile var freeFallMinMs = 120L
        @Volatile var impactThreshold = 22.0f
        @Volatile var impactWindowMs = 1500L
        @Volatile var inactivitySeconds = 2.5f
        @Volatile var inactivityStdDev = 1.6

        @Volatile
        var isRunning: Boolean = false
            private set

        private val stateListeners = CopyOnWriteArrayList<(String, Boolean) -> Unit>()

        fun start(context: Context) {
            val intent = Intent(context, FallMonitoringService::class.java).apply {
                action = ACTION_START
            }
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, FallMonitoringService::class.java).apply {
                action = ACTION_STOP
            })
        }

        fun updateSettings(context: Context, ff: Double, ffMs: Int, impact: Double, windowMs: Int, inactSec: Double, stdev: Double) {
            context.startService(Intent(context, FallMonitoringService::class.java).apply {
                action = ACTION_UPDATE_SETTINGS
                putExtra("ff", ff)
                putExtra("ffMs", ffMs)
                putExtra("impact", impact)
                putExtra("windowMs", windowMs)
                putExtra("inactSec", inactSec)
                putExtra("stdev", stdev)
            })
        }

        fun addStateListener(listener: (String, Boolean) -> Unit) { stateListeners.add(listener) }
        fun removeStateListener(listener: (String, Boolean) -> Unit) { stateListeners.remove(listener) }
    }

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private lateinit var sensorThread: HandlerThread
    private lateinit var sensorHandler: Handler

    // Fall-detection state machine
    private var freeFallStartAt: Long? = null
    private var freeFallSeen = false
    private var lastFreeFallSeenAt: Long = 0
    private var impactSeenAt: Long? = null
    private var peakImpact = 0f
    private val stillnessWindow = ArrayDeque<Float>()
    private var unresolved = false
    private var lastBroadcastAt = 0L
    private val maxStillnessSamples = 120

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        sensorThread = HandlerThread("zeroui-sensors").apply { start() }
        sensorHandler = Handler(sensorThread.looper)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            "com.zeroui.app.action.CLEAR_UNRESOLVED" -> {
                // Dart has consumed the pending fall (PIN cancelled or the
                // emergency is now active): reset the detector.
                clearUnresolved()
                return START_STICKY
            }
            ACTION_STOP -> {
                unregisterListener()
                stopForegroundCompat()
                stopSelf()
                isRunning = false
                broadcastState("stopped", false)
                return START_NOT_STICKY
            }
            ACTION_UPDATE_SETTINGS -> {
                val ff = intent.getDoubleExtra("ff", freeFallThreshold.toDouble()).toFloat()
                val ffMs = intent.getIntExtra("ffMs", freeFallMinMs.toInt()).toLong()
                val impact = intent.getDoubleExtra("impact", impactThreshold.toDouble()).toFloat()
                val windowMs = intent.getIntExtra("windowMs", impactWindowMs.toInt()).toLong()
                val inactSec = intent.getDoubleExtra("inactSec", inactivitySeconds.toDouble()).toFloat()
                val stdev = intent.getDoubleExtra("stdev", inactivityStdDev)
                freeFallThreshold = ff
                freeFallMinMs = ffMs
                impactThreshold = impact
                impactWindowMs = windowMs
                inactivitySeconds = inactSec
                inactivityStdDev = stdev
                startForegroundCompat()
                return START_STICKY
            }
            else -> { // ACTION_START
                startForegroundCompat()
                registerListener()
                isRunning = true
                broadcastState("monitoring", false)
            }
        }
        return START_STICKY
    }

    private fun startForegroundCompat() {
        // Android 15+ (targetSdk 35+) enforces a 6h/day rolling budget on
        // dataSync services (Service.onTimeout → must stopSelf within
        // seconds), which an always-on fall monitor cannot survive. The
        // platform-sanctioned type for a legitimate long-running use case not
        // covered by another type is `specialUse`; it needs no type-specific
        // runtime permission, has no timeout on any current Android release,
        // and is declared with PROPERTY_SPECIAL_USE_FGS_SUBTYPE so its
        // purpose is explicit for system review. 'dataSync' described a use
        // case (file transfer) this service does not have.
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            buildNotification(),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R)
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            else 0
        )
        isRunning = true
    }

    private fun registerListener() {
        val sensor = accelerometer ?: run {
            broadcastState("unavailable", false)
            return
        }
        try {
            sensorManager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME, sensorHandler)
            broadcastState("healthy", false)
        } catch (_: Exception) {
            broadcastState("unavailable", false)
        }
    }

    private fun unregisterListener() {
        try { sensorManager.unregisterListener(this) } catch (_: Exception) {}
    }

    private fun broadcastState(state: String, unresolvedFlag: Boolean) {
        val intent = Intent(ACTION_STATE).apply {
            `package` = packageName
            putExtra(EXTRA_STATE, state)
            putExtra(EXTRA_UNRESOLVED, unresolvedFlag)
        }
        sendBroadcast(intent)
        stateListeners.forEach { it(state, unresolvedFlag) }
    }

    // ── SensorEventListener ───────────────────────────────────────────

    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type != Sensor.TYPE_ACCELEROMETER) return
        val x = event.values[0]; val y = event.values[1]; val z = event.values[2]
        val magnitude = sqrt(x * x + y * y + z * z)
        val now = System.currentTimeMillis()

        if (unresolved) {
            // Re-broadcast every 8 s while a fall waits for the app's response,
            // so a freshly restarted Dart isolate can recover the countdown.
            if (now - lastBroadcastAt >= 8000) {
                lastBroadcastAt = now
                broadcastState("unresolved", true)
            }
            return
        }

        // Phase 1 — free fall
        if (magnitude < freeFallThreshold) {
            if (freeFallStartAt == null) freeFallStartAt = now
            lastFreeFallSeenAt = now
            if (now - freeFallStartAt!! >= freeFallMinMs) freeFallSeen = true
        } else if (impactSeenAt == null) {
            freeFallStartAt = null
            if (freeFallSeen && lastFreeFallSeenAt > 0 && now - lastFreeFallSeenAt > impactWindowMs) {
                freeFallSeen = false
            }
        }

        // Phase 2 — impact with a plausible cause
        if (impactSeenAt == null) {
            val strongImpact = magnitude > impactThreshold
            val plausible = freeFallSeen || magnitude > impactThreshold * 1.8f
            if (strongImpact && plausible) {
                impactSeenAt = now
                peakImpact = magnitude
                stillnessWindow.clear()
                return
            }
        }

        // Phase 3 — stillness after impact
        if (impactSeenAt != null) {
            if (magnitude > peakImpact) peakImpact = magnitude
            stillnessWindow.addLast(magnitude)
            if (stillnessWindow.size > maxStillnessSamples) stillnessWindow.removeFirst()
            val presetMs = (inactivitySeconds * 1000).toLong()
            if (now - impactSeenAt!! >= presetMs) {
                evaluateStillness(now)
            }
        }
    }

    private fun evaluateStillness(impactAt: Long) {
        val threshold = impactThreshold * 1.5f
        val samples = stillnessWindow.filter { it < threshold }
        var ok = false
        var stdDev = 0.0
        if (samples.size >= 5) {
            val mean = samples.average()
            val variance = samples.map { (it - mean) * (it - mean) }.average()
            stdDev = sqrt(variance)
            ok = stdDev <= inactivityStdDev
        }
        val stillnessMs = System.currentTimeMillis() - impactAt
        if (ok) {
            unresolved = true
            lastBroadcastAt = 0
            val freefallMs = if (freeFallSeen && freeFallStartAt != null) impactAt - freeFallStartAt!! else 0L
            broadcastFall(peakImpact, freefallMs, stillnessMs, stdDev)
            stillnessWindow.clear()
        } else {
            // Not a fall; reset and keep monitoring.
        }
        freeFallStartAt = null
        freeFallSeen = false
        impactSeenAt = null
        peakImpact = 0f
    }

    /** A 12-second cooldown prevents the same event from re-firing repeatedly. */
    private var cooldownUntil = 0L

    private fun broadcastFall(impact: Float, freefallMs: Long, stillnessMs: Long, stdev: Double) {
        val now = System.currentTimeMillis()
        if (now < cooldownUntil) return
        cooldownUntil = now + 12_000
        val intent = Intent(ACTION_FALL_DETECTED).apply {
            `package` = packageName
            putExtra(EXTRA_IMPACT, impact)
            putExtra(EXTRA_FREEFALL_MS, freefallMs)
            putExtra(EXTRA_STILLNESS_MS, stillnessMs)
            putExtra(EXTRA_STDEV, stdev)
        }
        sendBroadcast(intent)
        broadcastState("unresolved", true)
    }

    /** Called by Dart once the countdown is cancelled or the emergency is active. */
    fun clearUnresolved() {
        unresolved = false
        freeFallStartAt = null
        freeFallSeen = false
        impactSeenAt = null
        peakImpact = 0f
        stillnessWindow.clear()
        if (isRunning) broadcastState("monitoring", false)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    override fun onDestroy() {
        unregisterListener()
        sensorThread.quitSafely()
        isRunning = false
        super.onDestroy()
    }

    private fun stopForegroundCompat() {
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        isRunning = false
    }

    private fun createChannel() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val existing = manager.getNotificationChannel(CHANNEL_ID) ?: NotificationChannel(
            CHANNEL_ID,
            "Fall monitoring",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Keeps fall detection running while the app is not on screen"
            setShowBadge(false)
        }
        manager.createNotificationChannel(existing)
    }

    private fun buildNotification(): Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Zero UI fall monitoring active")
            .setContentText("Detecting possible falls in the background")
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setContentIntent(openApp)
            .build()
    }
}
