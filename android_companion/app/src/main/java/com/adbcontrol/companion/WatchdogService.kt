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
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.Locale

class WatchdogService : Service() {

    companion object {
        private const val CHANNEL_ID = "adb_companion_watchdog"
        private const val NOTIF_ID = 1001
        private const val PERIODIC_MS = 15_000L
        private const val BEACON_PORT = 45454
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
            updateNotification(st)
            if (st.wifiConnected && st.isTrustedWifi) sendUdpBeacon(st)
            handler.postDelayed(this, PERIODIC_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        isRunning = true
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification("Monitoring Trusted Wi-Fi & Wireless Debugging..."))
        acquireWakeLock()
        registerMonitors()
        val st = SelfHealEngine.heal(this, "ServiceStart")
        updateNotification(st)
        if (st.wifiConnected && st.isTrustedWifi) sendUdpBeacon(st)
        HealWorker.ensureScheduled(this)
        HealAlarmReceiver.scheduleNext(this, 30_000L)
        handler.postDelayed(periodicTask, PERIODIC_MS)
    }

    private fun sendUdpBeacon(st: HealStatus) {
        if (!st.wifiConnected || !st.isTrustedWifi) return
        Thread {
            try {
                // Re-verify trusted Wi-Fi right before broadcasting
                if (!SelfHealEngine.isTrustedWifiConnected(this)) return@Thread
                val ip = SelfHealEngine.getWifiIpAddress(this)
                if (ip == "N/A") return@Thread
                val payload = JSONObject().apply {
                    put("app", "adb_companion")
                    put("model", Build.MODEL)
                    put("ip", ip)
                    put("bssid", st.currentBssid)
                    put("adb_wifi", st.adbWifiEnabled)
                }.toString().toByteArray(Charsets.UTF_8)
                DatagramSocket().use { sock ->
                    sock.broadcast = true
                    val pkt = DatagramPacket(payload, payload.size, InetAddress.getByName("255.255.255.255"), BEACON_PORT)
                    sock.send(pkt)
                }
            } catch (_: Exception) {
            }
        }.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val st = SelfHealEngine.heal(this, "ServicePing")
        updateNotification(st)
        if (st.wifiConnected && st.isTrustedWifi) sendUdpBeacon(st)
        HealAlarmReceiver.scheduleNext(this, 30_000L)
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (SelfHealEngine.isMasterEnabled(this)) {
            SelfHealEngine.appendLog(this, "App swiped away — scheduling auto-revive (2s)")
            HealAlarmReceiver.scheduleNext(this, 2_000L)
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        isRunning = false
        handler.removeCallbacksAndMessages(null)
        unregisterMonitors()
        releaseWakeLock()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun acquireWakeLock() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return
            if (wakeLock == null) {
                wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ADBCompanion::WatchdogLock").apply {
                    setReferenceCounted(false)
                }
            }
            if (wakeLock?.isHeld == false) wakeLock?.acquire()
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

    /** Multi-stage fast retry so Wi-Fi reconnects / manual OFF toggles heal and beacon quickly on Trusted Wi-Fi. */
    private fun scheduleFastHealRetries(tag: String, sendBeacon: Boolean = false) {
        for (delayMs in longArrayOf(1000L, 3500L, 8000L)) {
            handler.postDelayed({
                if (!isRunning) return@postDelayed
                val st = SelfHealEngine.heal(this@WatchdogService, tag)
                updateNotification(st)
                if (sendBeacon && st.wifiConnected && st.isTrustedWifi) {
                    sendUdpBeacon(st)
                }
            }, delayMs)
        }
    }

    private fun createWifiCallback(): ConnectivityManager.NetworkCallback {
        val handleCaps: (NetworkCapabilities) -> Unit = { caps ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val info = caps.transportInfo as? WifiInfo
                val bssid = info?.bssid?.trim()?.uppercase(Locale.US)
                if (!bssid.isNullOrBlank() && bssid != "02:00:00:00:00:00") {
                    SelfHealEngine.cachedNetworkBssid = bssid
                }
            }
        }
        val handleAvailable: () -> Unit = {
            scheduleFastHealRetries("Wi-Fi Connected", sendBeacon = true)
        }
        val handleLost: () -> Unit = {
            SelfHealEngine.cachedNetworkBssid = null
            handler.post {
                SelfHealEngine.appendLog(this@WatchdogService, "Wi-Fi lost — waiting for Trusted Wi-Fi")
                updateNotification(SelfHealEngine.heal(this@WatchdogService, "Wi-Fi Lost"))
            }
        }

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
                override fun onAvailable(network: Network) = handleAvailable()
                override fun onCapabilitiesChanged(network: Network, networkCapabilities: NetworkCapabilities) =
                    handleCaps(networkCapabilities)
                override fun onLost(network: Network) = handleLost()
            }
        } else {
            object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = handleAvailable()
                override fun onCapabilitiesChanged(network: Network, networkCapabilities: NetworkCapabilities) =
                    handleCaps(networkCapabilities)
                override fun onLost(network: Network) = handleLost()
            }
        }
    }

    private fun registerMonitors() {
        // 1. Instant Wi-Fi connect callback (with FLAG_INCLUDE_LOCATION_INFO on Android 12+)
        try {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val req = NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .build()
            val cb = createWifiCallback()
            cm.registerNetworkCallback(req, cb)
            networkCallback = cb
        } catch (_: Exception) {
        }

        // 2. Instant ContentObserver on `adb_wifi_enabled` with multi-retry (Trusted Wi-Fi only)
        try {
            val obs = object : ContentObserver(handler) {
                override fun onChange(selfChange: Boolean) {
                    if (SelfHealEngine.isAutoAdbWifiEnabled(this@WatchdogService) &&
                        !SelfHealEngine.isAdbWifiEnabled(this@WatchdogService) &&
                        SelfHealEngine.isTrustedWifiConnected(this@WatchdogService)
                    ) {
                        scheduleFastHealRetries("ADB Wi-Fi OFF")
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
                    scheduleFastHealRetries(tag, sendBeacon = true)
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
            description = "Keeps Wireless Debugging active automatically on Trusted Wi-Fi"
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
        val txt = when {
            !st.hasPermission -> "Needs 1-time ADB WRITE_SECURE_SETTINGS grant"
            st.wifiConnected && st.isTrustedWifi && st.adbWifiEnabled -> "🟢 Trusted Wi-Fi & Wireless Debugging Active"
            st.wifiConnected && !st.isTrustedWifi && st.currentBssid.isEmpty() -> "📍 Turn ON Location to verify Trusted Wi-Fi"
            st.wifiConnected && !st.isTrustedWifi -> "🔒 Untrusted Wi-Fi — Beacon & ADB paused"
            !st.wifiEnabled -> "⚠️ Wi-Fi OFF — waiting for Wi-Fi"
            !st.wifiConnected -> "⏳ Connecting to Wi-Fi..."
            else -> "🔄 Re-enabling Wireless Debugging..."
        }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID, buildNotification(txt))
    }
}
