package com.zeroui.app

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodChannel

/**
 * Bridge between Dart and the native emergency foreground service.
 *
 * Channel: `in.zeroui/foreground`
 *   start  {title, text}          → start (or restart) the emergency foreground service
 *   update {title, text}          → refresh the ongoing notification
 *   stop                          → stop the service
 *   isRunning                     → bool
 *   platformInfo                  → {sdkInt, osVersion, manufacturer, model}
 *
 * Channel: `in.zeroui/monitoring`
 *   start {settings?}             → start the passive fall-monitoring service
 *   stop {cancel?}                → stop monitoring; cancel=true clears pending-fall state
 *   setSettings {ff, ffMs, ...}   → push user thresholds to the native detector
 *   isRunning                     → {running, state, unresolved}
 *   clearPendingFall              → Dart consumed the countdown (cancelled or active);
 *                                   resets the native detector's unresolved flag
 *
 * Events pushed TO Dart (device-to-Dart, BackgroundChannel-style via
 * `in.zeroui/monitoring/events` invokeMethod):
 *   fallDetected {impact, freefall_ms, stillness_ms, stdev, unresolved}
 *   monitorState {state, unresolved}
 */
class MainActivity : FlutterActivity() {

    private val channelName = "in.zeroui/foreground"
    private val monitorChannel = "in.zeroui/monitoring"
    private val monitorEventsChannel = "in.zeroui/monitoring/events"
    private var permissionBridge: PermissionBridge? = null
    private var flutterEngineRef: FlutterEngine? = null
    private var fallReceiver: BroadcastReceiver? = null
    private var stateReceiver: BroadcastReceiver? = null
    private var sosReceiver: BroadcastReceiver? = null
    private var receiverToken: Any? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        flutterEngineRef = flutterEngine

        // Native runtime-permission handling (replaces permission_handler, whose
        // Android plugin requires a newer AGP than this project ships).
        permissionBridge = PermissionBridge(this, flutterEngine.dartExecutor.binaryMessenger)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "start" -> {
                        val title = call.argument<String>("title") ?: "Zero UI emergency active"
                        val text = call.argument<String>("text") ?: "Emergency session running"
                        try {
                            EmergencyForegroundService.start(applicationContext, title, text)
                            result.success(true)
                        } catch (error: Exception) {
                            result.error("start_failed", error.message, null)
                        }
                    }

                    "update" -> {
                        val title = call.argument<String>("title") ?: "Zero UI emergency active"
                        val text = call.argument<String>("text") ?: "Emergency session running"
                        try {
                            if (EmergencyForegroundService.isRunning) {
                                EmergencyForegroundService.update(applicationContext, title, text)
                            }
                            result.success(true)
                        } catch (error: Exception) {
                            result.error("update_failed", error.message, null)
                        }
                    }

                    "stop" -> {
                        EmergencyForegroundService.stop(applicationContext)
                        result.success(true)
                    }

                    "isRunning" -> result.success(EmergencyForegroundService.isRunning)

                    "platformInfo" -> result.success(
                        mapOf(
                            "sdkInt" to Build.VERSION.SDK_INT,
                            "osVersion" to Build.VERSION.RELEASE,
                            "manufacturer" to Build.MANUFACTURER,
                            "model" to Build.MODEL
                        )
                    )

                    else -> result.notImplemented()
                }
            }

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, monitorChannel)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "start" -> {
                        applySettings(call)
                        try {
                            FallMonitoringService.start(applicationContext)
                            result.success(true)
                        } catch (error: Exception) {
                            result.error("monitor_start_failed", error.message, null)
                        }
                    }
                    "stop" -> {
                        if (call.argument<Boolean>("cancel") == false &&
                            FallMonitoringService.isRunning) {
                            // When Dart *confirms the fall is consumed* (cancelled or
                            // an active emergency) it calls clearPendingFall instead;
                            // plain stop() simply ends monitoring.
                        }
                        FallMonitoringService.stop(applicationContext)
                        result.success(true)
                    }
                    "setSettings" -> {
                        applySettings(call)
                        result.success(true)
                    }
                    "isRunning" -> result.success(
                        mapOf(
                            "running" to FallMonitoringService.isRunning,
                            "state" to if (FallMonitoringService.isRunning) "monitoring" else "stopped",
                        )
                    )
                    "clearPendingFall" -> {
                        // Handled via a self-broadcast since the service instance may
                        // not be directly reachable.
                        sendBroadcast(
                            Intent(this, FallMonitoringService::class.java).apply {
                                action = "com.zeroui.app.action.CLEAR_UNRESOLVED"
                            }
                        )
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }

        installFallReceiver(flutterEngine.dartExecutor.binaryMessenger)
    }

    private fun applySettings(call: io.flutter.plugin.common.MethodCall) {
        val ff = call.argument<Double>("freeFallThreshold") ?: 4.0
        val ffMs = call.argument<Int>("freeFallMinMs") ?: 120
        val impact = call.argument<Double>("impactThreshold") ?: 22.0
        val windowMs = call.argument<Int>("impactWindowMs") ?: 1500
        val inactSec = call.argument<Double>("inactivitySeconds") ?: 2.5
        val stdev = call.argument<Double>("inactivityStdDev") ?: 1.6
        FallMonitoringService.updateSettings(
            applicationContext, ff, ffMs, impact, windowMs, inactSec, stdev
        )
    }

    /** Forward native fall events to Dart, even when the app is in the background. */
    @SuppressLint("UnspecifiedRegisterReceiverFlags")
    private fun installFallReceiver(messenger: BinaryMessenger) {
        fallReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action != FallMonitoringService.ACTION_FALL_DETECTED) return
                val payload = mapOf(
                    "impact" to intent.getFloatExtra(FallMonitoringService.EXTRA_IMPACT, 0f).toDouble(),
                    "freefall_ms" to intent.getLongExtra(FallMonitoringService.EXTRA_FREEFALL_MS, 0),
                    "stillness_ms" to intent.getLongExtra(FallMonitoringService.EXTRA_STILLNESS_MS, 0),
                    "stdev" to intent.getDoubleExtra(FallMonitoringService.EXTRA_STDEV, 0.0),
                    "unresolved" to true,
                )
                Handler(Looper.getMainLooper()).post {
                    MethodChannel(messenger, monitorEventsChannel).invokeMethod(
                        "fallDetected", payload
                    )
                }
            }
        }
        stateReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action != FallMonitoringService.ACTION_STATE) return
                val payload = mapOf(
                    "state" to intent.getStringExtra(FallMonitoringService.EXTRA_STATE),
                    "unresolved" to intent.getBooleanExtra(FallMonitoringService.EXTRA_UNRESOLVED, false),
                )
                Handler(Looper.getMainLooper()).post {
                    MethodChannel(messenger, monitorEventsChannel).invokeMethod(
                        "monitorState", payload
                    )
                }
            }
        }
        val filter = IntentFilter().apply {
            addAction(FallMonitoringService.ACTION_FALL_DETECTED)
            addAction(FallMonitoringService.ACTION_STATE)
        }
        // SOS gesture from the accessibility service (fires while backgrounded
        // or locked — same shared emergency state machine as a fall).
        sosReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action != SosAccessibilityService.ACTION_SOS_TRIGGERED) return
                val payload = mapOf(
                    "trigger_source" to intent.getStringExtra(SosAccessibilityService.EXTRA_TRIGGER_SOURCE),
                    "detected_at" to System.currentTimeMillis(),
                )
                Handler(Looper.getMainLooper()).post {
                    MethodChannel(messenger, monitorEventsChannel).invokeMethod(
                        "sosTriggered", payload
                    )
                }
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(fallReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
            registerReceiver(stateReceiver, IntentFilter().apply {
                addAction(FallMonitoringService.ACTION_STATE)
            }, ContextCompat.RECEIVER_NOT_EXPORTED)
            registerReceiver(sosReceiver, IntentFilter().apply {
                addAction(SosAccessibilityService.ACTION_SOS_TRIGGERED)
            }, ContextCompat.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(fallReceiver, filter)
            @Suppress("DEPRECATION")
            registerReceiver(stateReceiver, IntentFilter().apply {
                addAction(FallMonitoringService.ACTION_STATE)
            })
            @Suppress("DEPRECATION")
            registerReceiver(sosReceiver, IntentFilter().apply {
                addAction(SosAccessibilityService.ACTION_SOS_TRIGGERED)
            })
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Tapping the monitoring notification reopens the app; nothing else needed.
    }

    override fun onDestroy() {
        try { fallReceiver?.let { unregisterReceiver(it) } } catch (_: Exception) {}
        try { stateReceiver?.let { unregisterReceiver(it) } } catch (_: Exception) {}
        try { sosReceiver?.let { unregisterReceiver(it) } } catch (_: Exception) {}
        fallReceiver = null
        stateReceiver = null
        sosReceiver = null
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        permissionBridge?.onRequestPermissionsResult(requestCode, grantResults)
    }
}
