package com.zeroui.app

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Runtime permissions, implemented natively
 * ========================================
 * `permission_handler_android` 14.x ships a Gradle script that requires AGP 9,
 * which this project does not use, so the emergency app talks to the Android
 * permission APIs directly instead. That also lets us report the exact status
 * words the setup screens show.
 *
 * Channel: `in.zeroui/permissions`
 *   status([])                       → {key: granted|denied|restricted|notRequired}
 *   request([key, ...])              → same map, after the system dialogs
 *   isIgnoringBatteryOptimizations() → bool
 *   requestIgnoreBatteryOptimizations() → bool
 *   openSettings()                   → opens this app's system settings page
 */
class PermissionBridge(private val activity: Activity, messenger: BinaryMessenger) :
    MethodChannel.MethodCallHandler {

    companion object {
        const val CHANNEL = "in.zeroui/permissions"
        const val REQUEST_CODE = 4731

        /** Dart key → Android permission. */
        val KEY_TO_PERMISSION: Map<String, String> = mapOf(
            "notifications" to Manifest.permission.POST_NOTIFICATIONS,
            "location_when_in_use" to Manifest.permission.ACCESS_FINE_LOCATION,
            "location_coarse" to Manifest.permission.ACCESS_COARSE_LOCATION,
            "location_always" to Manifest.permission.ACCESS_BACKGROUND_LOCATION,
            "microphone" to Manifest.permission.RECORD_AUDIO,
        )

        const val GRANTED = "granted"
        const val DENIED = "denied"
        const val RESTRICTED = "restricted" // must be changed in system settings
        const val NOT_REQUIRED = "notRequired"
    }

    private val channel = MethodChannel(messenger, CHANNEL).also { it.setMethodCallHandler(this) }

    private var pendingResult: MethodChannel.Result? = null
    private var pendingKeys: List<String> = emptyList()

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "status" -> result.success(statusFor(call.argument<List<String>>("permissions") ?: emptyList()))
            "request" -> requestPermissions(call.argument<List<String>>("permissions") ?: emptyList(), result)
            "isIgnoringBatteryOptimizations" -> result.success(isIgnoringBatteryOptimizations())
            "requestIgnoreBatteryOptimizations" -> {
                requestIgnoreBatteryOptimizations()
                result.success(isIgnoringBatteryOptimizations())
            }
            "openSettings" -> {
                openAppSettings()
                result.success(true)
            }
            else -> result.notImplemented()
        }
    }

    /** Called from the Activity when the system dialog finishes. */
    fun onRequestPermissionsResult(requestCode: Int, grantResults: IntArray) {
        if (requestCode != REQUEST_CODE) return
        val result = pendingResult ?: return
        pendingResult = null
        val keys = pendingKeys
        pendingKeys = emptyList()
        result.success(statusFor(keys))
    }

    private fun statusFor(keys: List<String>): Map<String, String> {
        val statuses = HashMap<String, String>()
        for (key in keys) {
            statuses[key] = when {
                key == "battery_optimisation" ->
                    if (isIgnoringBatteryOptimizations()) GRANTED else DENIED
                // Android < 13 has no notification runtime permission.
                key == "notifications" && Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU -> GRANTED
                else -> {
                    val permission = KEY_TO_PERMISSION[key] ?: continue
                    statusOf(permission)
                }
            }
        }
        return statuses
    }

    private fun statusOf(permission: String): String {
        val granted = ContextCompat.checkSelfPermission(activity, permission) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) return GRANTED
        // Once the system stops showing the dialog the user has to change it in
        // settings: report that explicitly instead of silently retrying.
        val canAsk = ActivityCompat.shouldShowRequestPermissionRationale(activity, permission)
        return if (canAsk) DENIED else RESTRICTED
    }

    private fun requestPermissions(keys: List<String>, result: MethodChannel.Result) {
        if (pendingResult != null) {
            result.error("busy", "Another permission request is already running", null)
            return
        }

        val permissions = keys
            .filter { it != "battery_optimisation" }
            .mapNotNull { KEY_TO_PERMISSION[it] }
            .filter { Build.VERSION.SDK_INT >= 33 || it != Manifest.permission.POST_NOTIFICATIONS }
            .distinct()
            .toTypedArray()

        if (permissions.isEmpty()) {
            // Nothing runtime-requestable (or only the battery exemption).
            if (keys.contains("battery_optimisation") && !isIgnoringBatteryOptimizations()) {
                requestIgnoreBatteryOptimizations()
            }
            result.success(statusFor(keys))
            return
        }

        pendingResult = result
        pendingKeys = keys
        ActivityCompat.requestPermissions(activity, permissions, REQUEST_CODE)
    }

    private fun isIgnoringBatteryOptimizations(): Boolean {
        val power = activity.getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return false
        return power.isIgnoringBatteryOptimizations(activity.packageName)
    }

    private fun requestIgnoreBatteryOptimizations() {
        try {
            val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                data = Uri.parse("package:${activity.packageName}")
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            activity.startActivity(intent)
        } catch (_: Exception) {
            openAppSettings()
        }
    }

    private fun openAppSettings() {
        try {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.parse("package:${activity.packageName}")
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            activity.startActivity(intent)
        } catch (_: Exception) {
            // Nothing sensible left to do; the setup screen already explains it.
        }
    }
}
