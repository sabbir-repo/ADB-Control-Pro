package com.adbcontrol.companion

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.BatteryManager
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
    const val KEY_TG_ENABLED = "tg_enabled"
    const val KEY_TG_TOKEN = "tg_token"
    const val KEY_TG_CHAT_ID = "tg_chat_id"
    const val KEY_TG_OFFSET = "tg_offset"
    private const val KEY_LOGS = "event_logs"

    const val DEFAULT_BOT_TOKEN = "9099338399:AAEes3DIHY69U0I3n_PA7om0EpW8CvCf1rg"

    private fun prefs(context: Context) =
        context.createDeviceProtectedStorageContext()
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun isMasterEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_MASTER, true)
    fun setMasterEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_MASTER, v).apply()

    fun isAutoWifiEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTO_WIFI, false)
    fun setAutoWifiEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_AUTO_WIFI, v).apply()

    // Default OFF so Wireless Debugging is controlled On-Demand via Telegram Bot (can still be toggled ON)
    fun isAutoAdbWifiEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_AUTO_ADB_WIFI, false)
    fun setAutoAdbWifiEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_AUTO_ADB_WIFI, v).apply()

    fun isTelegramEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_TG_ENABLED, true)
    fun setTelegramEnabled(context: Context, v: Boolean) = prefs(context).edit().putBoolean(KEY_TG_ENABLED, v).apply()

    fun getBotToken(context: Context): String =
        (prefs(context).getString(KEY_TG_TOKEN, DEFAULT_BOT_TOKEN) ?: DEFAULT_BOT_TOKEN).trim()

    fun setBotToken(context: Context, token: String) =
        prefs(context).edit().putString(KEY_TG_TOKEN, token.trim().ifEmpty { DEFAULT_BOT_TOKEN }).apply()

    fun getAllowedChatId(context: Context): String =
        (prefs(context).getString(KEY_TG_CHAT_ID, "") ?: "").trim()

    fun setAllowedChatId(context: Context, chatId: String) =
        prefs(context).edit().putString(KEY_TG_CHAT_ID, chatId.trim()).apply()

    fun getTelegramOffset(context: Context): Long = prefs(context).getLong(KEY_TG_OFFSET, 0L)
    fun setTelegramOffset(context: Context, offset: Long) = prefs(context).edit().putLong(KEY_TG_OFFSET, offset).apply()

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

    fun getBatteryLevel(context: Context): Int {
        return try {
            val bm = context.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
            bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
        } catch (_: Exception) {
            -1
        }
    }

    fun getWifiIpAddress(context: Context): String {
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces()
            while (ifaces != null && ifaces.hasMoreElements()) {
                val iface = ifaces.nextElement()
                if (!iface.isUp || iface.isLoopback) continue
                if (!iface.name.startsWith("wlan")) continue
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

    /** Direct ON/OFF toggle used by Telegram Bot commands (/adb_on, /adb_off). */
    @Synchronized
    fun setAdbWifi(context: Context, enabled: Boolean, source: String = "Telegram"): String {
        if (!hasSecureSettingsPermission(context)) {
            val msg = "❌ WRITE_SECURE_SETTINGS পারমিশন গ্রান্ট করা নেই।"
            appendLog(context, "$source → $msg")
            return msg
        }
        if (enabled && !isWifiConnected(context)) {
            val msg = "⚠️ ফোনে Wi-Fi কানেক্টেড নেই—আগে Wi-Fi কানেক্ট করুন।"
            appendLog(context, "$source → $msg")
            return msg
        }
        return try {
            if (enabled) {
                Settings.Global.putInt(context.contentResolver, Settings.Global.ADB_ENABLED, 1)
                Settings.Global.putInt(context.contentResolver, "adb_wifi_enabled", 1)
                val ip = getWifiIpAddress(context)
                val msg = "✅ Wireless Debugging ON\n🌐 Wi-Fi IP: $ip"
                appendLog(context, "$source → Wireless Debugging ON ($ip)")
                msg
            } else {
                Settings.Global.putInt(context.contentResolver, "adb_wifi_enabled", 0)
                val msg = "🛑 Wireless Debugging OFF করা হয়েছে।"
                appendLog(context, "$source → Wireless Debugging OFF")
                msg
            }
        } catch (e: Exception) {
            val msg = "❌ Error: ${e.message}"
            appendLog(context, "$source → $msg")
            msg
        }
    }

    @Synchronized
    fun tryEnableWifi(context: Context, source: String = "Telegram"): String {
        if (isWifiEnabled(context)) return "📶 Wi-Fi ইতিমধ্যেই ON আছে (IP: ${getWifiIpAddress(context)})।"
        try {
            val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            @Suppress("DEPRECATION")
            wm?.setWifiEnabled(true)
        } catch (_: Exception) {
        }
        if (hasSecureSettingsPermission(context)) {
            try {
                Settings.Global.putInt(context.contentResolver, "wifi_on", 1)
            } catch (_: Exception) {
            }
        }
        val nowOn = isWifiEnabled(context)
        val msg = if (nowOn) "✅ Wi-Fi ON করা হয়েছে।" else "⚠️ Android 10+ সিকিউরিটির কারণে ব্যাকগ্রাউন্ড থেকে Wi-Fi ON ব্লক আছে।"
        appendLog(context, "$source → $msg")
        return msg
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

        if (!wifiOn && isAutoWifiEnabled(context)) {
            tryEnableWifi(context, trigger)
            wifiOn = isWifiEnabled(context)
            wifiConn = isWifiConnected(context)
            actions.add(if (wifiOn) "Wi-Fi ON" else "Wi-Fi OFF")
        }

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
                    actions.add(if (adbWifi) "Wireless Debugging → ON" else "adb_wifi_enabled set")
                } catch (e: Exception) {
                    actions.add("Error: ${e.message}")
                }
            }
        }

        val summary = if (actions.isEmpty()) {
            "OK ($trigger)"
        } else {
            "$trigger → ${actions.joinToString(" | ")}"
        }

        if (actions.isNotEmpty() || trigger == "Manual" || trigger == "Boot") {
            appendLog(context, summary)
        }
        return HealStatus(hasPerm, wifiOn, wifiConn, adbWifi, summary)
    }
}
