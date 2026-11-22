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
import android.text.InputType
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    private lateinit var tvHeaderBadge: TextView
    private lateinit var valPerm: TextView
    private lateinit var valWifi: TextView
    private lateinit var valTrusted: TextView
    private lateinit var valAdb: TextView
    private lateinit var valSvc: TextView

    private lateinit var tvConnectedMac: TextView
    private lateinit var etTrustedMac: EditText
    private lateinit var tvLocHint: TextView
    private lateinit var btnFixLoc: Button
    private lateinit var tvLogs: TextView

    private val grantCmd =
        "pm grant com.adbcontrol.companion android.permission.WRITE_SECURE_SETTINGS"

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        window.statusBarColor = Color.parseColor("#0B1120")
        window.navigationBarColor = Color.parseColor("#0B1120")

        requestRuntimePermissions()

        val root = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#0B1120"))
            isVerticalScrollBarEnabled = false
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(18), dp(16), dp(28))
        }
        root.addView(container)

        // ── Header ───────────────────────────────────────────────
        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(4) }
        }
        headerRow.addView(TextView(this).apply {
            text = "⚡ ADB Companion"
            textSize = 21f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#F8FAFC"))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        tvHeaderBadge = TextView(this).apply {
            text = "● RUNNING"
            textSize = 11f
            setTypeface(null, Typeface.BOLD)
            setPadding(dp(10), dp(4), dp(10), dp(4))
        }
        headerRow.addView(tvHeaderBadge)
        container.addView(headerRow)

        container.addView(TextView(this).apply {
            text = "Trusted Wi-Fi LAN Beacon & Offline Wireless ADB Auto-Heal"
            textSize = 12.5f
            setTextColor(Color.parseColor("#94A3B8"))
            setPadding(0, 0, 0, dp(14))
        })

        // ── Card 1: Live System Status ───────────────────────────
        val statusCard = makeCard()
        statusCard.addView(makeSectionTitle("📊 System Status"))
        valPerm = addStatusRow(statusCard, "Secure Settings")
        valWifi = addStatusRow(statusCard, "Wi-Fi & IP")
        valTrusted = addStatusRow(statusCard, "Trusted Wi-Fi")
        valAdb = addStatusRow(statusCard, "Wireless Debugging")
        valSvc = addStatusRow(statusCard, "Watchdog Service", last = true)
        container.addView(statusCard)

        // ── Card 2: Trusted Wi-Fi (Router MAC / BSSID) ───────────
        val wifiCard = makeCard()
        wifiCard.addView(makeSectionTitle("📶 Trusted Wi-Fi (Router MAC)"))
        wifiCard.addView(TextView(this).apply {
            text = "UDP Beacon & Auto-ADB run only when connected to this router MAC."
            textSize = 11.5f
            setTextColor(Color.parseColor("#94A3B8"))
            setPadding(0, 0, 0, dp(10))
        })

        // Connected MAC inset box with Copy + Use buttons
        val connBox = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = makeRoundedBg("#0B1120", "#1E293B", 12)
            setPadding(dp(12), dp(10), dp(10), dp(10))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(10) }
        }
        val connLeft = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        connLeft.addView(TextView(this).apply {
            text = "CONNECTED WI-FI MAC (BSSID)"
            textSize = 10f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#64748B"))
        })
        tvConnectedMac = TextView(this).apply {
            text = "Checking..."
            textSize = 13.5f
            typeface = Typeface.MONOSPACE
            setTypeface(Typeface.MONOSPACE, Typeface.BOLD)
            setTextColor(Color.parseColor("#38BDF8"))
            setPadding(0, dp(2), 0, 0)
        }
        connLeft.addView(tvConnectedMac)
        connBox.addView(connLeft)

        connBox.addView(makeMiniBtn("📋 Copy", "#1E293B", "#E2E8F0") {
            val mac = SelfHealEngine.getConnectedWifiBssid(this)
            if (mac.isEmpty()) {
                Toast.makeText(this, "No readable Wi-Fi MAC (check Wi-Fi & Location)", Toast.LENGTH_SHORT).show()
            } else {
                copyText("Wi-Fi MAC", mac)
                Toast.makeText(this, "Copied: $mac", Toast.LENGTH_SHORT).show()
            }
        })
        connBox.addView(makeMiniBtn("⬇ Use", "#0F766E", "#ECFEFF", leftMarginDp = 6) {
            val mac = SelfHealEngine.getConnectedWifiBssid(this)
            if (mac.isEmpty()) {
                Toast.makeText(this, "Connect to Wi-Fi & turn ON Location first", Toast.LENGTH_SHORT).show()
            } else {
                etTrustedMac.setText(mac)
                Toast.makeText(this, "Filled connected MAC — tap Save", Toast.LENGTH_SHORT).show()
            }
        })
        wifiCard.addView(connBox)

        // Editable Trusted MAC input + Save button row
        val inputRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }
        etTrustedMac = EditText(this).apply {
            setText(SelfHealEngine.getTrustedBssidRaw(this@MainActivity))
            hint = "58:D9:D5:68:0A:B0 (blank = any)"
            setHintTextColor(Color.parseColor("#64748B"))
            setTextColor(Color.parseColor("#F8FAFC"))
            textSize = 13f
            typeface = Typeface.MONOSPACE
            isSingleLine = true
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            background = makeRoundedBg("#0B1120", "#334155", 10)
            setPadding(dp(12), dp(10), dp(12), dp(10))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        inputRow.addView(etTrustedMac)
        inputRow.addView(makeMiniBtn("💾 Save", "#10B981", "#052E16", leftMarginDp = 8) {
            SelfHealEngine.setTrustedBssid(this, etTrustedMac.text.toString())
            etTrustedMac.setText(SelfHealEngine.getTrustedBssidRaw(this))
            val saved = SelfHealEngine.getTrustedBssidRaw(this).ifEmpty { "Any Wi-Fi" }
            SelfHealEngine.appendLog(this, "Trusted Wi-Fi MAC saved: $saved")
            Toast.makeText(this, "✅ Saved: $saved", Toast.LENGTH_SHORT).show()
            refreshUi()
        })
        wifiCard.addView(inputRow)

        // Conditional Location / Permission hint (hidden when everything is OK)
        tvLocHint = TextView(this).apply {
            textSize = 11.5f
            setTextColor(Color.parseColor("#FBBF24"))
            setPadding(0, dp(8), 0, dp(4))
            visibility = View.GONE
        }
        wifiCard.addView(tvLocHint)
        btnFixLoc = makeMiniBtn("📍 Enable Location / Permission", "#78350F", "#FEF3C7").apply {
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(4) }
            setOnClickListener {
                if (!SelfHealEngine.hasLocationPermission(this@MainActivity)) {
                    requestRuntimePermissions()
                } else {
                    startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
                }
            }
        }
        wifiCard.addView(btnFixLoc)
        container.addView(wifiCard)

        // ── Card 3: Auto-Heal Switches ───────────────────────────
        val cfgCard = makeCard()
        cfgCard.addView(makeSectionTitle("⚙️ Watchdog Controls"))
        cfgCard.addView(makeSwitch("🛡️ Master Auto-Heal Watchdog", SelfHealEngine.isMasterEnabled(this)) { on ->
            SelfHealEngine.setMasterEnabled(this, on)
            if (on) startWatchdog() else stopWatchdog()
            refreshUi()
        })
        cfgCard.addView(makeDivider())
        cfgCard.addView(makeSwitch("📡 Auto-Enable Wireless Debugging", SelfHealEngine.isAutoAdbWifiEnabled(this)) { on ->
            SelfHealEngine.setAutoAdbWifiEnabled(this, on)
        })
        cfgCard.addView(makeDivider())
        cfgCard.addView(makeSwitch("📶 Try Auto-Enable Wi-Fi (if OFF)", SelfHealEngine.isAutoWifiEnabled(this)) { on ->
            SelfHealEngine.setAutoWifiEnabled(this, on)
        })
        container.addView(cfgCard)

        // ── Card 4: Compact 2x2 Quick Actions ────────────────────
        val actCard = makeCard()
        actCard.addView(makeSectionTitle("🚀 Quick Actions"))

        val row1 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(8) }
        }
        row1.addView(makeGridBtn("⚡ Heal Now", "#10B981", "#052E16", rightMarginDp = 4) {
            val st = SelfHealEngine.heal(this, "Manual")
            if (SelfHealEngine.isMasterEnabled(this)) startWatchdog()
            Toast.makeText(this, st.summary, Toast.LENGTH_SHORT).show()
            refreshUi()
        })
        row1.addView(makeGridBtn("🔋 Battery Free", "#2563EB", "#FFFFFF", leftMarginDp = 4) {
            requestIgnoreBatteryOptimizations()
        })
        actCard.addView(row1)

        val row2 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }
        row2.addView(makeGridBtn("⚙️ App Info", "#1E293B", "#E2E8F0", rightMarginDp = 4) {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.parse("package:$packageName")
            }
            startActivity(intent)
        })
        row2.addView(makeGridBtn("📋 Copy ADB Cmd", "#1E293B", "#E2E8F0", leftMarginDp = 4) {
            copyText("ADB Grant", grantCmd)
            Toast.makeText(this, "Copied ADB grant command!", Toast.LENGTH_SHORT).show()
        })
        actCard.addView(row2)
        container.addView(actCard)

        // ── Card 5: Live Event Log ───────────────────────────────
        val logCard = makeCard()
        val logHeader = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(8) }
        }
        logHeader.addView(TextView(this).apply {
            text = "📜 Recent Activity"
            textSize = 14f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#E2E8F0"))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        logHeader.addView(makeMiniBtn("Clear", "#1E293B", "#94A3B8") {
            SelfHealEngine.clearLogs(this)
            refreshUi()
        })
        logCard.addView(logHeader)

        tvLogs = TextView(this).apply {
            textSize = 11.5f
            typeface = Typeface.MONOSPACE
            setTextColor(Color.parseColor("#CBD5E1"))
            background = makeRoundedBg("#0B1120", "#1E293B", 10)
            setPadding(dp(10), dp(10), dp(10), dp(10))
            setLineSpacing(dp(2).toFloat(), 1.1f)
        }
        logCard.addView(tvLogs)
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

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refreshUi()
    }

    private fun requestRuntimePermissions() {
        val needed = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (!SelfHealEngine.hasLocationPermission(this)) {
            needed.add(Manifest.permission.ACCESS_FINE_LOCATION)
            needed.add(Manifest.permission.ACCESS_COARSE_LOCATION)
        }
        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toTypedArray(), 101)
        }
    }

    private fun copyText(label: String, text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText(label, text))
    }

    private fun startWatchdog() {
        HealWorker.ensureScheduled(this)
        try {
            ContextCompat.startForegroundService(this, Intent(this, WatchdogService::class.java))
        } catch (_: Exception) {
        }
    }

    private fun stopWatchdog() {
        HealWorker.cancel(this)
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
        val ip = SelfHealEngine.getWifiIpAddress(this)
        val bssid = SelfHealEngine.getConnectedWifiBssid(this)
        val trusted = SelfHealEngine.isTrustedWifiConnected(this)
        val hasLocPerm = SelfHealEngine.hasLocationPermission(this)
        val locOn = SelfHealEngine.isLocationEnabled(this)

        // Header badge
        if (svc && trusted && adbWifi) {
            tvHeaderBadge.text = "● ACTIVE"
            tvHeaderBadge.setTextColor(Color.parseColor("#34D399"))
            tvHeaderBadge.background = makeRoundedBg("#064E3B", "#10B981", 20)
        } else if (svc) {
            tvHeaderBadge.text = "● STANDBY"
            tvHeaderBadge.setTextColor(Color.parseColor("#FBBF24"))
            tvHeaderBadge.background = makeRoundedBg("#451A03", "#D97706", 20)
        } else {
            tvHeaderBadge.text = "● STOPPED"
            tvHeaderBadge.setTextColor(Color.parseColor("#94A3B8"))
            tvHeaderBadge.background = makeRoundedBg("#1E293B", "#334155", 20)
        }

        // Status rows
        setPill(valPerm, if (perm) "Granted" else "Missing", perm)
        setPill(
            valWifi,
            when {
                wifiConn -> ip
                wifiOn -> "Not Connected"
                else -> "OFF"
            },
            wifiConn,
        )
        setPill(
            valTrusted,
            when {
                !wifiConn -> "No Wi-Fi"
                trusted -> "Matched ✓"
                bssid.isEmpty() -> "Location Needed"
                else -> "Untrusted"
            },
            trusted,
        )
        setPill(valAdb, if (adbWifi) "ON" else "OFF", adbWifi)
        setPill(valSvc, if (svc) "Running" else "Stopped", svc)

        // Connected MAC display
        tvConnectedMac.text = when {
            !wifiConn -> "Not connected to Wi-Fi"
            bssid.isNotEmpty() -> bssid
            !hasLocPerm -> "Permission needed"
            !locOn -> "Turn ON Location (GPS)"
            else -> "Unavailable"
        }
        tvConnectedMac.setTextColor(
            Color.parseColor(
                when {
                    bssid.isNotEmpty() && trusted -> "#34D399"
                    bssid.isNotEmpty() -> "#38BDF8"
                    else -> "#FBBF24"
                },
            ),
        )

        // Contextual hint ONLY if Location permission or GPS toggle is missing
        if (wifiConn && bssid.isEmpty()) {
            tvLocHint.visibility = View.VISIBLE
            btnFixLoc.visibility = View.VISIBLE
            tvLocHint.text = if (!hasLocPerm) {
                "⚠️ Android requires Location permission to read router MAC (BSSID)."
            } else {
                "⚠️ Turn ON phone Location (GPS) so Android reveals router MAC (BSSID)."
            }
        } else {
            tvLocHint.visibility = View.GONE
            btnFixLoc.visibility = View.GONE
        }

        tvLogs.text = SelfHealEngine.getLogs(this)
    }

    private fun setPill(tv: TextView, label: String, ok: Boolean) {
        tv.text = label
        tv.setTextColor(Color.parseColor(if (ok) "#34D399" else "#F87171"))
    }

    private fun makeRoundedBg(fillHex: String, strokeHex: String, radiusDp: Int): GradientDrawable {
        return GradientDrawable().apply {
            setColor(Color.parseColor(fillHex))
            cornerRadius = dp(radiusDp).toFloat()
            setStroke(dp(1), Color.parseColor(strokeHex))
        }
    }

    private fun makeCard(): LinearLayout {
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeRoundedBg("#11192C", "#1E293B", 16)
            setPadding(dp(14), dp(14), dp(14), dp(14))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(12) }
        }
    }

    private fun makeSectionTitle(title: String): TextView {
        return TextView(this).apply {
            text = title
            textSize = 14f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#E2E8F0"))
            setPadding(0, 0, 0, dp(8))
        }
    }

    private fun addStatusRow(parent: LinearLayout, label: String, last: Boolean = false): TextView {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(5), 0, dp(5))
        }
        row.addView(TextView(this).apply {
            text = label
            textSize = 13f
            setTextColor(Color.parseColor("#94A3B8"))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        val valueTv = TextView(this).apply {
            textSize = 13f
            setTypeface(null, Typeface.BOLD)
            gravity = Gravity.END
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
        }
        row.addView(valueTv)
        parent.addView(row)
        if (!last) parent.addView(makeDivider())
        return valueTv
    }

    private fun makeDivider(): View {
        return View(this).apply {
            setBackgroundColor(Color.parseColor("#1E293B"))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(1),
            ).apply {
                topMargin = dp(2)
                bottomMargin = dp(2)
            }
        }
    }

    private fun makeSwitch(label: String, checked: Boolean, onToggle: (Boolean) -> Unit): Switch {
        return Switch(this).apply {
            text = label
            isChecked = checked
            setTextColor(Color.parseColor("#E2E8F0"))
            textSize = 13.5f
            setPadding(0, dp(8), 0, dp(8))
            setOnCheckedChangeListener { _, isChecked -> onToggle(isChecked) }
        }
    }

    private fun makeMiniBtn(
        label: String,
        bgHex: String,
        fgHex: String,
        leftMarginDp: Int = 0,
        onClick: () -> Unit = {},
    ): Button {
        return Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 12f
            minWidth = 0
            minimumWidth = 0
            minHeight = 0
            minimumHeight = 0
            background = GradientDrawable().apply {
                setColor(Color.parseColor(bgHex))
                cornerRadius = dp(9).toFloat()
            }
            setTextColor(Color.parseColor(fgHex))
            setTypeface(null, Typeface.BOLD)
            setPadding(dp(11), dp(8), dp(11), dp(8))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { leftMargin = dp(leftMarginDp) }
            setOnClickListener { onClick() }
        }
    }

    private fun makeGridBtn(
        label: String,
        bgHex: String,
        fgHex: String,
        leftMarginDp: Int = 0,
        rightMarginDp: Int = 0,
        onClick: () -> Unit,
    ): Button {
        return Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 13f
            minHeight = dp(42)
            background = GradientDrawable().apply {
                setColor(Color.parseColor(bgHex))
                cornerRadius = dp(11).toFloat()
            }
            setTextColor(Color.parseColor(fgHex))
            setTypeface(null, Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f,
            ).apply {
                leftMargin = dp(leftMarginDp)
                rightMargin = dp(rightMarginDp)
            }
            setOnClickListener { onClick() }
        }
    }
}
