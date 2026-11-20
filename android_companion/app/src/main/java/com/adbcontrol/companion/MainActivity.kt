package com.adbcontrol.companion

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    private lateinit var tvStatus: TextView
    private lateinit var tvLogs: TextView

    private val grantCmd =
        "adb shell pm grant com.adbcontrol.companion android.permission.WRITE_SECURE_SETTINGS"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 101)
        }

        val root = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#0B1120"))
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(40, 48, 40, 48)
        }
        root.addView(container)

        // Header
        container.addView(TextView(this).apply {
            text = "⚡ ADB Control Companion"
            textSize = 22f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#10B981"))
        })
        container.addView(TextView(this).apply {
            text = "Auto-Heal Wireless Debugging after Reboot / Wi-Fi / Toggle OFF"
            textSize = 13f
            setTextColor(Color.parseColor("#94A3B8"))
            setPadding(0, 6, 0, 24)
        })

        // Status Card
        val statusCard = makeCard()
        tvStatus = TextView(this).apply {
            textSize = 14f
            setTextColor(Color.parseColor("#F8FAFC"))
            setLineSpacing(8f, 1.1f)
        }
        statusCard.addView(tvStatus)
        container.addView(statusCard)

        // Switches Card
        val cfgCard = makeCard()
        cfgCard.addView(makeSwitch("🛡️ Master Auto-Heal Watchdog", SelfHealEngine.isMasterEnabled(this)) { on ->
            SelfHealEngine.setMasterEnabled(this, on)
            if (on) startWatchdog() else stopWatchdog()
            refreshUi()
        })
        cfgCard.addView(makeSwitch("📡 Auto-Enable Wireless Debugging", SelfHealEngine.isAutoAdbWifiEnabled(this)) { on ->
            SelfHealEngine.setAutoAdbWifiEnabled(this, on)
        })
        cfgCard.addView(makeSwitch("📶 Try Auto-Enable Wi-Fi (if OFF)", SelfHealEngine.isAutoWifiEnabled(this)) { on ->
            SelfHealEngine.setAutoWifiEnabled(this, on)
        })
        container.addView(cfgCard)

        // Actions Card
        val actCard = makeCard()
        actCard.addView(makeButton("⚡ Heal & Enable Now", "#10B981", "#052E16") {
            val st = SelfHealEngine.heal(this, "Manual")
            if (SelfHealEngine.isMasterEnabled(this)) startWatchdog()
            Toast.makeText(this, st.summary, Toast.LENGTH_SHORT).show()
            refreshUi()
        })
        actCard.addView(makeButton("🔋 Allow Unrestricted Battery (Important)", "#3B82F6", "#FFFFFF") {
            requestIgnoreBatteryOptimizations()
        })
        actCard.addView(makeButton("🚀 Open App Info / Enable Auto-Start", "#0EA5E9", "#FFFFFF") {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.parse("package:$packageName")
            }
            startActivity(intent)
        })
        actCard.addView(makeButton("📋 Copy 1-Time ADB Grant Command", "#334155", "#F8FAFC") {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("ADB Grant", grantCmd))
            Toast.makeText(this, "Copied ADB grant command!", Toast.LENGTH_SHORT).show()
        })
        container.addView(actCard)

        // Live Event Log Card
        val logCard = makeCard()
        logCard.addView(TextView(this).apply {
            text = "📜 Self-Heal Event Log"
            textSize = 14f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#10B981"))
            setPadding(0, 0, 0, 10)
        })
        tvLogs = TextView(this).apply {
            textSize = 12f
            typeface = Typeface.MONOSPACE
            setTextColor(Color.parseColor("#CBD5E1"))
        }
        logCard.addView(tvLogs)
        logCard.addView(makeButton("🧹 Clear Log", "#1E293B", "#94A3B8") {
            SelfHealEngine.clearLogs(this)
            refreshUi()
        })
        container.addView(logCard)

        setContentView(root)

        if (SelfHealEngine.isMasterEnabled(this)) {
            startWatchdog()
        }
        refreshUi()
    }

    override fun onResume() {
        super.onResume()
        refreshUi()
    }

    private fun startWatchdog() {
        try {
            ContextCompat.startForegroundService(this, Intent(this, WatchdogService::class.java))
        } catch (_: Exception) {
        }
    }

    private fun stopWatchdog() {
        HealAlarmReceiver.cancel(this)
        stopService(Intent(this, WatchdogService::class.java))
    }

    private fun requestIgnoreBatteryOptimizations() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:$packageName")
                }
                startActivity(intent)
            } else {
                Toast.makeText(this, "✅ Battery Optimization already Unrestricted!", Toast.LENGTH_SHORT).show()
            }
        } catch (_: Exception) {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    private fun refreshUi() {
        val perm = SelfHealEngine.hasSecureSettingsPermission(this)
        val wifiOn = SelfHealEngine.isWifiEnabled(this)
        val wifiConn = SelfHealEngine.isWifiConnected(this)
        val adbWifi = SelfHealEngine.isAdbWifiEnabled(this)
        val svc = WatchdogService.isRunning

        tvStatus.text = buildString {
            appendLine("🔐 WRITE_SECURE_SETTINGS : ${if (perm) "✅ Granted" else "❌ Not Granted (Run ADB Grant)"}")
            appendLine("📶 Wi-Fi Status          : ${when { wifiConn -> "🟢 Connected"; wifiOn -> "🟡 ON (Not Connected)"; else -> "🔴 OFF" }}")
            appendLine("📡 Wireless Debugging    : ${if (adbWifi) "🟢 ON" else "🔴 OFF"}")
            append("🛡️ Watchdog Service      : ${if (svc) "🟢 Running" else "⚪ Stopped"}")
        }
        tvLogs.text = SelfHealEngine.getLogs(this)
    }

    private fun makeCard(): LinearLayout {
        val bg = GradientDrawable().apply {
            setColor(Color.parseColor("#11192C"))
            cornerRadius = 28f
            setStroke(2, Color.parseColor("#334155"))
        }
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = bg
            setPadding(32, 28, 32, 28)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { setMargins(0, 0, 0, 24) }
        }
    }

    private fun makeSwitch(label: String, checked: Boolean, onToggle: (Boolean) -> Unit): Switch {
        return Switch(this).apply {
            text = label
            isChecked = checked
            setTextColor(Color.parseColor("#F8FAFC"))
            textSize = 14f
            setPadding(0, 12, 0, 12)
            setOnCheckedChangeListener { _, isChecked -> onToggle(isChecked) }
        }
    }

    private fun makeButton(label: String, bgHex: String, fgHex: String, onClick: () -> Unit): Button {
        val bg = GradientDrawable().apply {
            setColor(Color.parseColor(bgHex))
            cornerRadius = 20f
        }
        return Button(this).apply {
            text = label
            isAllCaps = false
            background = bg
            setTextColor(Color.parseColor(fgHex))
            setTypeface(null, Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { setMargins(0, 10, 0, 10) }
            setOnClickListener { onClick() }
        }
    }
}
