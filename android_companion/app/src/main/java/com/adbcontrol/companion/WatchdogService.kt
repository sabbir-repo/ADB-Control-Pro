package com.adbcontrol.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.database.ContentObserver
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.NotificationCompat

class WatchdogService : Service() {

    companion object {
        private const val CHANNEL_ID = "adb_companion_watchdog"
        private const val NOTIF_ID = 1001
        private const val PERIODIC_MS = 30_000L
        const val ACTION_RESTART_BOT = "com.adbcontrol.companion.ACTION_RESTART_BOT"
        @Volatile
        var isRunning: Boolean = false
    }

    private val handler = Handler(Looper.getMainLooper())
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var adbObserver: ContentObserver? = null
    private var sysReceiver: BroadcastReceiver? = null
    private var wakeLock: PowerManager.WakeLock? = null

    private val periodicTask = object : Runnable {
        override fun run() {
            if (!isRunning) return
            val st = SelfHealEngine.heal(this@WatchdogService, "Periodic")
            if (SelfHealEngine.isTelegramEnabled(this@WatchdogService) && !TelegramBotController.isPolling) {
                TelegramBotController.start(this@WatchdogService) {
                    handler.post { updateNotification(SelfHealEngine.heal(this@WatchdogService, "TGUpdate")) }
                }
            }
            updateNotification(st)
            handler.postDelayed(this, PERIODIC_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        isRunning = true
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification("Monitoring Wi-Fi & Telegram Bot..."))
        acquireWakeLock()
        registerMonitors()
        val st = SelfHealEngine.heal(this, "ServiceStart")
        updateNotification(st)
        TelegramBotController.start(this) {
            handler.post { updateNotification(SelfHealEngine.heal(this, "TGUpdate")) }
        }
        HealAlarmReceiver.scheduleNext(this, 30_000L)
        handler.postDelayed(periodicTask, PERIODIC_MS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_RESTART_BOT) {
            Thread {
                TelegramBotController.restart(this) {
                    handler.post { updateNotification(SelfHealEngine.heal(this, "TGUpdate")) }
                }
            }.start()
        } else if (SelfHealEngine.isTelegramEnabled(this) && !TelegramBotController.isPolling) {
            TelegramBotController.start(this) {
                handler.post { updateNotification(SelfHealEngine.heal(this, "TGUpdate")) }
            }
        }
        val st = SelfHealEngine.heal(this, "ServicePing")
        updateNotification(st)
        HealAlarmReceiver.scheduleNext(this, 30_000L)
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // When user swipes app out of Recents (especially on Tecno HiOS), schedule fast revival!
        if (SelfHealEngine.isMasterEnabled(this)) {
            SelfHealEngine.appendLog(this, "App swiped away — scheduling auto-revive (2s)")
            HealAlarmReceiver.scheduleNext(this, 2_000L)
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        isRunning = false
        handler.removeCallbacks(periodicTask)
        TelegramBotController.stop()
        unregisterMonitors()
        releaseWakeLock()
        super.onDestroy()
    }

    private fun acquireWakeLock() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return
            if (wakeLock == null) {
                wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ADBCompanion::TelegramBotLock").apply {
                    setReferenceCounted(false)
                }
            }
            if (wakeLock?.isHeld == false) {
                wakeLock?.acquire()
            }
        } catch (_: Exception) {
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Exception) {
        }
        wakeLock = null
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun registerMonitors() {
        // 1. Instant Wi-Fi connect callback
        try {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val req = NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .build()
            val cb = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    handler.postDelayed({
                        val st = SelfHealEngine.heal(this@WatchdogService, "Wi-Fi Connected")
                        updateNotification(st)
                    }, 1500L)
                }

                override fun onLost(network: Network) {
                    handler.post {
                        SelfHealEngine.appendLog(this@WatchdogService, "Wi-Fi lost — waiting to auto-heal")
                        updateNotification(SelfHealEngine.heal(this@WatchdogService, "Wi-Fi Lost"))
                    }
                }
            }
            cm.registerNetworkCallback(req, cb)
            networkCallback = cb
        } catch (_: Exception) {
        }

        // 2. Instant ContentObserver on `adb_wifi_enabled` (re-enables if turned OFF!)
        try {
            val obs = object : ContentObserver(handler) {
                override fun onChange(selfChange: Boolean) {
                    if (!SelfHealEngine.isAdbWifiEnabled(this@WatchdogService) &&
                        SelfHealEngine.isWifiConnected(this@WatchdogService)
                    ) {
                        handler.postDelayed({
                            val st = SelfHealEngine.heal(this@WatchdogService, "ADB Wi-Fi Changed")
                            updateNotification(st)
                        }, 800L)
                    }
                }
            }
            contentResolver.registerContentObserver(
                Settings.Global.getUriFor("adb_wifi_enabled"),
                false,
                obs,
            )
            adbObserver = obs
        } catch (_: Exception) {
        }

        // 3. Screen unlock (USER_PRESENT) + Wi-Fi state broadcast
        try {
            val rx = object : BroadcastReceiver() {
                override fun onReceive(ctx: Context, intent: Intent) {
                    val tag = if (intent.action == Intent.ACTION_USER_PRESENT) "Unlock" else "Wi-Fi State"
                    handler.postDelayed({
                        val st = SelfHealEngine.heal(this@WatchdogService, tag)
                        updateNotification(st)
                    }, 1000L)
                }
            }
            val filter = IntentFilter().apply {
                addAction(Intent.ACTION_USER_PRESENT)
                addAction(WifiManager.WIFI_STATE_CHANGED_ACTION)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                registerReceiver(rx, filter, RECEIVER_NOT_EXPORTED)
            } else {
                @Suppress("DEPRECATION")
                registerReceiver(rx, filter)
            }
            sysReceiver = rx
        } catch (_: Exception) {
        }
    }

    private fun unregisterMonitors() {
        networkCallback?.let {
            try {
                (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).unregisterNetworkCallback(it)
            } catch (_: Exception) {
            }
        }
        adbObserver?.let {
            try {
                contentResolver.unregisterContentObserver(it)
            } catch (_: Exception) {
            }
        }
        sysReceiver?.let {
            try {
                unregisterReceiver(it)
            } catch (_: Exception) {
            }
        }
    }

    private fun createNotificationChannel() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val ch = NotificationChannel(
            CHANNEL_ID,
            "ADB Companion Watchdog",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Keeps Wireless Debugging active automatically on Wi-Fi"
            setShowBadge(false)
        }
        nm.createNotificationChannel(ch)
    }

    private fun buildNotification(text: String): Notification {
        val openIntent = Intent(this, MainActivity::class.java)
        val pi = PendingIntent.getActivity(
            this,
            0,
            openIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("⚡ ADB Control Companion")
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(pi)
            .build()
    }

    private fun updateNotification(st: HealStatus) {
        val botOn = SelfHealEngine.isTelegramEnabled(this)
        val txt = when {
            !st.hasPermission -> "Needs 1-time ADB WRITE_SECURE_SETTINGS grant"
            botOn -> "🤖 Telegram Bot Ready | ADB Wi-Fi: ${if (st.adbWifiEnabled) "🟢 ON" else "🔴 OFF"}"
            st.wifiConnected && st.adbWifiEnabled -> "🟢 Wi-Fi & Wireless Debugging Active"
            !st.wifiEnabled -> "⚠️ Wi-Fi OFF — waiting for Wi-Fi"
            else -> "⏳ Monitoring Wi-Fi & Wireless Debugging"
        }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID, buildNotification(txt))
    }
}
