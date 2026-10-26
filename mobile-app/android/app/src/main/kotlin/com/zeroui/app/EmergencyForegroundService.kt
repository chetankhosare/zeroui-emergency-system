package com.zeroui.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/**
 * Zero UI emergency foreground service
 * ====================================
 * Android only keeps an app's process (and therefore its location updates,
 * microphone capture and uploads) alive while it is in the foreground or while
 * a foreground service is running.  This service is started when an emergency
 * becomes active and stopped when it ends.
 *
 * Service types declared (see AndroidManifest): location | microphone | dataSync
 * — matching the three things an active emergency actually does. Android 14+
 * requires the *runtime* permissions for those types to be granted, which the
 * app's onboarding flow enforces before the service can start.
 *
 * The service intentionally holds no emergency logic of its own: the Dart
 * isolate owns that. Its only jobs are (a) keeping the process alive and
 * (b) showing an always-visible status notification.
 */
class EmergencyForegroundService : Service() {

    companion object {
        const val CHANNEL_ID = "zeroui_emergency"
        const val NOTIFICATION_ID = 4711

        const val ACTION_START = "com.zeroui.app.action.START"
        const val ACTION_UPDATE = "com.zeroui.app.action.UPDATE"
        const val ACTION_STOP = "com.zeroui.app.action.STOP"

        const val EXTRA_TITLE = "title"
        const val EXTRA_TEXT = "text"

        @Volatile
        var isRunning: Boolean = false
            private set

        fun start(context: Context, title: String, text: String) {
            val intent = Intent(context, EmergencyForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_TITLE, title)
                putExtra(EXTRA_TEXT, text)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun update(context: Context, title: String, text: String) {
            val intent = Intent(context, EmergencyForegroundService::class.java).apply {
                action = ACTION_UPDATE
                putExtra(EXTRA_TITLE, title)
                putExtra(EXTRA_TEXT, text)
            }
            context.startService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, EmergencyForegroundService::class.java))
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopForegroundCompat()
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                val title = intent?.getStringExtra(EXTRA_TITLE) ?: "Zero UI emergency active"
                val text = intent?.getStringExtra(EXTRA_TEXT)
                    ?: "Sending location, audio and status updates"
                val notification = buildNotification(title, text)
                // Android 14 requires an explicit foreground service type when started.
                ServiceCompat.startForeground(
                    this,
                    NOTIFICATION_ID,
                    notification,
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION or
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
                    } else {
                        0
                    }
                )
                isRunning = true
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        isRunning = false
        super.onDestroy()
    }

    private fun stopForegroundCompat() {
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        isRunning = false
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Emergency session",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Keeps emergency location, audio and uploads running"
            setShowBadge(true)
            enableVibration(true)
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(title: String, text: String): Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_warning)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setContentIntent(openApp)
            .build()
    }
}
