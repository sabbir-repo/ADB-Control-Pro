package com.adbcontrol.companion

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
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
    val summary: String,
)

object SelfHealEngine {
    private const val PREFS_NAME = "adb_companion_prefs"
    const val KEY_MASTER = "auto_master"
    const val KEY_AUTO_WIFI = "auto_wifi"
    const val KEY_AUTO_ADB_WIFI = "auto_adb_wifi"
    private const val KEY_LOGS = "event_logs"

    private fun prefs(context: Context) =
        context.createDeviceProtectedStorageContext()
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun isMasterEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_MASTER, true)
    fun setMasterEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_MASTER, v).apply()

    fun isAutoWifiEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTO_WIFI, true)
    fun setAutoWifiEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_AUTO_WIFI, v).apply()

    fun isAutoAdbWifiEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTO_ADB_WIFI, true)
    fun setAutoAdbWifiEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_AUTO_ADB_WIFI, v).apply()

    fun hasSecureSettingsPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_SECURE_SETTINGS) ==
            PackageManager.PERMISSION_GRANTED

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

        if (!isMasterEnabled(context) && trigger != "Manual") {
            return HealStatus(hasPerm, wifiOn, wifiConn, adbWifi, "Master switch OFF")
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
            actions.add(if (wifiOn) "Wi-Fi ON" else "Wi-Fi OFF")
        }

        // 2. If Wi-Fi is connected and Wireless Debugging is OFF -> turn ON immediately
        if (isAutoAdbWifiEnabled(context) || trigger == "Manual") {
            if (!hasPerm) {
                actions.add("Missing WRITE_SECURE_SETTINGS")
            } else if (!wifiConn) {
                actions.add("Waiting for Wi-Fi")
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
            "OK ($trigger): Wi-Fi & Wireless Debugging active"
        } else {
            "$trigger → ${actions.joinToString(" | ")}"
        }

        if (actions.isNotEmpty() || trigger == "Manual" || trigger == "Boot") {
            appendLog(context, summary)
        }
        return HealStatus(hasPerm, wifiOn, wifiConn, adbWifi, summary)
    }
}
