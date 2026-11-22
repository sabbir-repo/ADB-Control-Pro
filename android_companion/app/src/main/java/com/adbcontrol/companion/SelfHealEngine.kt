package com.adbcontrol.companion

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.provider.Settings
import androidx.core.content.ContextCompat
import java.net.Inet4Address
import java.net.NetworkInterface
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class HealStatus(
    val hasPermission: Boolean,
    val wifiEnabled: Boolean,
    val wifiConnected: Boolean,
    val adbWifiEnabled: Boolean,
    val currentBssid: String,
    val isTrustedWifi: Boolean,
    val summary: String,
)

object SelfHealEngine {
    private const val PREFS_NAME = "adb_companion_prefs"
    const val KEY_MASTER = "auto_master"
    const val KEY_AUTO_WIFI = "auto_wifi"
    const val KEY_AUTO_ADB_WIFI = "auto_adb_wifi"
    const val KEY_TRUSTED_BSSID = "trusted_wifi_bssid"
    const val DEFAULT_TRUSTED_BSSID = "58:D9:D5:68:0A:B0"
    private const val KEY_LOGS = "event_logs"
    private const val MASKED_BSSID = "02:00:00:00:00:00"

    @Volatile
    var cachedNetworkBssid: String? = null

    private fun prefs(context: Context) =
        context.createDeviceProtectedStorageContext()
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun isMasterEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_MASTER, true)
    fun setMasterEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_MASTER, v).apply()

    fun isAutoWifiEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTO_WIFI, true)
    fun setAutoWifiEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_AUTO_WIFI, v).apply()

    fun isAutoAdbWifiEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTO_ADB_WIFI, true)
    fun setAutoAdbWifiEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_AUTO_ADB_WIFI, v).apply()

    fun getTrustedBssidRaw(context: Context): String {
        val p = prefs(context)
        return if (p.contains(KEY_TRUSTED_BSSID)) {
            p.getString(KEY_TRUSTED_BSSID, "") ?: ""
        } else {
            DEFAULT_TRUSTED_BSSID
        }
    }

    fun setTrustedBssid(context: Context, raw: String) {
        val normalized = raw
            .split(",", ";", "\n")
            .map { it.trim().uppercase(Locale.US) }
            .filter { it.isNotEmpty() }
            .distinct()
            .joinToString(", ")
        prefs(context).edit().putString(KEY_TRUSTED_BSSID, normalized).apply()
    }

    fun getTrustedBssidList(context: Context): List<String> =
        getTrustedBssidRaw(context)
            .split(",", ";", "\n")
            .map { it.trim().uppercase(Locale.US) }
            .filter { it.isNotEmpty() }

    fun hasSecureSettingsPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_SECURE_SETTINGS) ==
            PackageManager.PERMISSION_GRANTED

    fun hasLocationPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    fun isLocationEnabled(context: Context): Boolean {
        return try {
            val lm = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return false
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                lm.isLocationEnabled
            } else {
                @Suppress("DEPRECATION")
                Settings.Secure.getInt(
                    context.contentResolver,
                    Settings.Secure.LOCATION_MODE,
                    Settings.Secure.LOCATION_MODE_OFF,
                ) != Settings.Secure.LOCATION_MODE_OFF
            }
        } catch (_: Exception) {
            false
        }
    }

    fun isWifiEnabled(context: Context): Boolean {
        val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        return wm?.isWifiEnabled == true
    }

    fun isWifiConnected(context: Context): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return false
        val net = cm.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(net) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }

    private fun isValidBssid(raw: String?): Boolean {
        if (raw.isNullOrBlank()) return false
        val cleaned = raw.trim().uppercase(Locale.US)
        if (cleaned == MASKED_BSSID || cleaned == "00:00:00:00:00:00") return false
        return cleaned.length == 17 && cleaned.count { it == ':' } == 5
    }

    fun getConnectedWifiBssid(context: Context): String {
        if (!isWifiConnected(context)) return ""

        // 1. Modern Android 10+/12+ ConnectivityManager transportInfo
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                val caps = cm?.getNetworkCapabilities(cm.activeNetwork)
                val info = caps?.transportInfo as? WifiInfo
                val bssid = info?.bssid?.trim()?.uppercase(Locale.US)
                if (isValidBssid(bssid)) {
                    cachedNetworkBssid = bssid
                    return bssid!!
                }
            }
        } catch (_: Exception) {
        }

        // 2. Fallback to WifiManager.connectionInfo
        try {
            val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            @Suppress("DEPRECATION")
            val bssid = wm?.connectionInfo?.bssid?.trim()?.uppercase(Locale.US)
            if (isValidBssid(bssid)) {
                cachedNetworkBssid = bssid
                return bssid!!
            }
        } catch (_: Exception) {
        }

        // 3. Cached BSSID from NetworkCallback(FLAG_INCLUDE_LOCATION_INFO)
        val cached = cachedNetworkBssid
        if (isValidBssid(cached)) return cached!!

        return ""
    }

    fun isTrustedWifiConnected(context: Context): Boolean {
        if (!isWifiConnected(context)) return false
        val allowed = getTrustedBssidList(context)
        if (allowed.isEmpty()) return true
        val current = getConnectedWifiBssid(context)
        return current.isNotEmpty() && allowed.any { it.equals(current, ignoreCase = true) }
    }

    fun isAdbWifiEnabled(context: Context): Boolean =
        try {
            Settings.Global.getInt(context.contentResolver, "adb_wifi_enabled", 0) == 1
        } catch (_: Exception) {
            false
        }

    fun getWifiIpAddress(context: Context): String {
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces()
            while (ifaces != null && ifaces.hasMoreElements()) {
                val iface = ifaces.nextElement()
                if (!iface.isUp || iface.isLoopback || !iface.name.startsWith("wlan")) continue
                val addrs = iface.inetAddresses
                while (addrs.hasMoreElements()) {
                    val addr = addrs.nextElement()
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        return addr.hostAddress ?: "N/A"
                    }
                }
            }
        } catch (_: Exception) {
        }
        return "N/A"
    }

    @Synchronized
    fun appendLog(context: Context, msg: String) {
        val ts = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
        val line = "[$ts] $msg"
        val existing = prefs(context).getString(KEY_LOGS, "") ?: ""
        val lines = (if (existing.isBlank()) emptyList() else existing.lines()) + line
        val trimmed = lines.takeLast(40).joinToString("\n")
        prefs(context).edit().putString(KEY_LOGS, trimmed).apply()
    }

    fun getLogs(context: Context): String =
        prefs(context).getString(KEY_LOGS, "Ready — waiting for events...") ?: ""

    fun clearLogs(context: Context) {
        prefs(context).edit().putString(KEY_LOGS, "").apply()
    }

    @Synchronized
    fun heal(context: Context, trigger: String): HealStatus {
        val hasPerm = hasSecureSettingsPermission(context)
        var wifiOn = isWifiEnabled(context)
        var wifiConn = isWifiConnected(context)
        var adbWifi = isAdbWifiEnabled(context)
        var currentBssid = getConnectedWifiBssid(context)
        var trustedWifi = isTrustedWifiConnected(context)

        if (!isMasterEnabled(context) && trigger != "Manual") {
            return HealStatus(hasPerm, wifiOn, wifiConn, adbWifi, currentBssid, trustedWifi, "Master switch OFF")
        }

        val actions = mutableListOf<String>()

        // 1. If Wi-Fi is OFF, attempt enabling Wi-Fi where OS permits
        if (!wifiOn && isAutoWifiEnabled(context)) {
            try {
                val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
                @Suppress("DEPRECATION")
                wm?.setWifiEnabled(true)
            } catch (_: Exception) {
            }
            if (hasPerm) {
                try {
                    Settings.Global.putInt(context.contentResolver, "wifi_on", 1)
                } catch (_: Exception) {
                }
            }
            wifiOn = isWifiEnabled(context)
            wifiConn = isWifiConnected(context)
            currentBssid = getConnectedWifiBssid(context)
            trustedWifi = isTrustedWifiConnected(context)
            actions.add(if (wifiOn) "Wi-Fi ON" else "Wi-Fi OFF")
        }

        // 2. If Trusted Wi-Fi is connected and Wireless Debugging is OFF -> turn ON immediately
        if (isAutoAdbWifiEnabled(context) || trigger == "Manual") {
            if (!hasPerm) {
                actions.add("Missing WRITE_SECURE_SETTINGS")
            } else if (!wifiConn) {
                actions.add("Waiting for Wi-Fi")
            } else if (!trustedWifi && trigger != "Manual") {
                if (trigger != "Periodic" && trigger != "ServicePing") {
                    val label = currentBssid.ifEmpty { "Hidden/Location OFF" }
                    actions.add("Untrusted Wi-Fi ($label) — skipped")
                }
            } else if (!adbWifi) {
                try {
                    Settings.Global.putInt(context.contentResolver, Settings.Global.ADB_ENABLED, 1)
                    Settings.Global.putInt(context.contentResolver, "adb_wifi_enabled", 1)
                    adbWifi = isAdbWifiEnabled(context)
                    val ip = getWifiIpAddress(context)
                    actions.add(if (adbWifi) "Wireless Debugging → ON ($ip)" else "adb_wifi_enabled set")
                } catch (e: Exception) {
                    actions.add("Error: ${e.message}")
                }
            }
        }

        val summary = if (actions.isEmpty()) {
            when {
                wifiConn && !trustedWifi -> "Paused ($trigger): Untrusted Wi-Fi"
                else -> "OK ($trigger): Trusted Wi-Fi & Wireless Debugging active"
            }
        } else {
            "$trigger → ${actions.joinToString(" | ")}"
        }

        if (actions.isNotEmpty() || trigger == "Manual" || trigger == "Boot") {
            appendLog(context, summary)
        }
        return HealStatus(hasPerm, wifiOn, wifiConn, adbWifi, currentBssid, trustedWifi, summary)
    }
}
