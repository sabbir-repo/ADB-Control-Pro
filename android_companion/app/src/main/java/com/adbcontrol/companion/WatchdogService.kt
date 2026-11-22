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
        private const val BEACON_INTERVAL_MS = 30_000L
        private const val OFF_SIGNAL_DELAY_MS = 10_000L
        private const val WIFI_CONNECT_DELAY_MS = 3_000L
        private const val BEACON_PORT = 45454
        @Volatile
        var isRunning: Boolean = false
        @Volatile
        var isPendingReEnable: Boolean = false
    }

    private val handler = Handler(Looper.getMainLooper())
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var adbObserver: ContentObserver? = null
    private var sysReceiver: BroadcastReceiver? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var pendingReason: String = "Signal"

    private fun readCurrentStatus(summary: String): HealStatus {
        val hasPerm = SelfHealEngine.hasSecureSettingsPermission(this)
        val wifiOn = SelfHealEngine.isWifiEnabled(this)
        val wifiConn = SelfHealEngine.isWifiConnected(this)
        val adbWifi = SelfHealEngine.isAdbWifiEnabled(this)
        val bssid = if (wifiConn) SelfHealEngine.getConnectedWifiBssid(this) else ""
        val trusted = wifiConn && SelfHealEngine.isTrustedWifiConnected(this)
        return HealStatus(hasPerm, wifiOn, wifiConn, adbWifi, bssid, trusted, summary)
    }

    /**
     * One-shot event-driven re-enable task.
     * Fires ONLY after an OFF / Wi-Fi-connect signal — NEVER polls continuously when ON.
     */
    private val reEnableTask = Runnable {
        isPendingReEnable = false
        if (!isRunning) return@Runnable
        if (!SelfHealEngine.isMasterEnabled(this) || !SelfHealEngine.isAutoAdbWifiEnabled(this)) {
            return@Runnable
        }

        val current = readCurrentStatus("SignalCheck($pendingReason)")
        if (!current.wifiConnected || !current.isTrustedWifi) {
            SelfHealEngine.appendLog(this, "10s check ($pendingReason): not on Trusted Wi-Fi — idle")
            updateNotification(current)
            return@Runnable
        }
        if (current.adbWifiEnabled) {
            // Already ON — do nothing and remain completely idle
            SelfHealEngine.appendLog(this, "10s check ($pendingReason): already ON — idle")
            updateNotification(current)
            sendUdpBeacon(current)
            return@Runnable
        }

        // Still OFF → turn Wireless Debugging back ON
        val st = SelfHealEngine.heal(this, "Signal($pendingReason)")
        updateNotification(st)
        if (st.adbWifiEnabled) {
            sendUdpBeacon(st)
        } else if (st.hasPermission && st.wifiConnected && st.isTrustedWifi) {
            // OS was still busy tearing down the previous socket; schedule one clean 10s retry
            scheduleSignalReEnable("Retry-$pendingReason", OFF_SIGNAL_DELAY_MS)
        }
    }

    /** Lightweight PC discovery beacon only — never touches Wireless Debugging settings. */
    private val beaconTask = object : Runnable {
        override fun run() {
            if (!isRunning) return
            val st = readCurrentStatus("Beacon")
            if (st.wifiConnected && st.isTrustedWifi && st.adbWifiEnabled) {
                sendUdpBeacon(st)
            }
            handler.postDelayed(this, BEACON_INTERVAL_MS)
        }
    }

    private fun scheduleSignalReEnable(reason: String, delayMs: Long) {
        handler.removeCallbacks(reEnableTask)
        pendingReason = reason
        isPendingReEnable = true
        val sec = (delayMs / 1000L).coerceAtLeast(1L)
        SelfHealEngine.appendLog(this, "Signal: $reason → checking in ${sec}s...")
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID, buildNotification("⏳ Wireless Debugging OFF — re-enabling in ${sec}s..."))
        handler.postDelayed(reEnableTask, delayMs)
    }

    private fun cancelPendingReEnable() {
        if (isPendingReEnable) {
            handler.removeCallbacks(reEnableTask)
            isPendingReEnable = false
        }
    }

    override fun onCreate() {
        super.onCreate()
        isRunning = true
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification("Monitoring Trusted Wi-Fi & Wireless Debugging signals..."))
        acquireWakeLock()
        registerMonitors()
        val st = SelfHealEngine.heal(this, "ServiceStart")
        updateNotification(st)
        if (st.wifiConnected && st.isTrustedWifi && st.adbWifiEnabled) sendUdpBeacon(st)
        HealWorker.ensureScheduled(this)
        HealAlarmReceiver.scheduleNext(this, 60_000L)
        handler.postDelayed(beaconTask, BEACON_INTERVAL_MS)
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
        if (!isPendingReEnable) {
            val st = SelfHealEngine.heal(this, "ServicePing")
            updateNotification(st)
            if (st.wifiConnected && st.isTrustedWifi && st.adbWifiEnabled) sendUdpBeacon(st)
        }
        HealAlarmReceiver.scheduleNext(this, 60_000L)
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
        isPendingReEnable = false
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
            handler.post {
                if (SelfHealEngine.isMasterEnabled(this@WatchdogService) &&
                    SelfHealEngine.isAutoAdbWifiEnabled(this@WatchdogService) &&
                    !SelfHealEngine.isAdbWifiEnabled(this@WatchdogService)
                ) {
                    scheduleSignalReEnable("Wi-Fi Connected", WIFI_CONNECT_DELAY_MS)
                } else {
                    val st = readCurrentStatus("Wi-Fi Connected")
                    updateNotification(st)
                    if (st.wifiConnected && st.isTrustedWifi && st.adbWifiEnabled) {
                        sendUdpBeacon(st)
                    }
                }
            }
        }
        val handleLost: () -> Unit = {
            SelfHealEngine.cachedNetworkBssid = null
            handler.post {
                cancelPendingReEnable()
                SelfHealEngine.appendLog(this@WatchdogService, "Signal: Wi-Fi lost — waiting for Trusted Wi-Fi")
                updateNotification(readCurrentStatus("Wi-Fi Lost"))
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
        // 1. Instant Wi-Fi connect/disconnect signal (with FLAG_INCLUDE_LOCATION_INFO on Android 12+)
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

        // 2. Event-driven ContentObserver on `adb_wifi_enabled` (listens to BOTH ON and OFF signals)
        try {
            val obs = object : ContentObserver(handler) {
                override fun onChange(selfChange: Boolean) {
                    if (!SelfHealEngine.isMasterEnabled(this@WatchdogService) ||
                        !SelfHealEngine.isAutoAdbWifiEnabled(this@WatchdogService)
                    ) {
                        return
                    }
                    val isOn = SelfHealEngine.isAdbWifiEnabled(this@WatchdogService)
                    if (isOn) {
                        // ON signal → cancel any pending timer and stay idle
                        cancelPendingReEnable()
                        SelfHealEngine.appendLog(this@WatchdogService, "Signal: Wireless Debugging ON")
                        val st = readCurrentStatus("Wireless Debugging ON")
                        updateNotification(st)
                        if (st.wifiConnected && st.isTrustedWifi) sendUdpBeacon(st)
                    } else if (SelfHealEngine.isTrustedWifiConnected(this@WatchdogService)) {
                        // OFF signal on Trusted Wi-Fi → schedule ONE clean 10-second check-and-enable
                        scheduleSignalReEnable("ADB Wi-Fi OFF", OFF_SIGNAL_DELAY_MS)
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
                    if (!SelfHealEngine.isMasterEnabled(this@WatchdogService) ||
                        !SelfHealEngine.isAutoAdbWifiEnabled(this@WatchdogService)
                    ) {
                        return
                    }
                    // Only act if currently OFF on Trusted Wi-Fi and no signal timer is already pending
                    if (!isPendingReEnable &&
                        SelfHealEngine.isTrustedWifiConnected(this@WatchdogService) &&
                        !SelfHealEngine.isAdbWifiEnabled(this@WatchdogService)
                    ) {
                        val tag = if (intent.action == Intent.ACTION_USER_PRESENT) "Unlock" else "Wi-Fi State"
                        scheduleSignalReEnable(tag, WIFI_CONNECT_DELAY_MS)
                    }
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
