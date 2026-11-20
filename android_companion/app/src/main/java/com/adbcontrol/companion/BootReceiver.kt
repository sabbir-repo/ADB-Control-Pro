package com.adbcontrol.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (
            action == Intent.ACTION_BOOT_COMPLETED ||
            action == Intent.ACTION_LOCKED_BOOT_COMPLETED ||
            action == Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            SelfHealEngine.appendLog(context, "System event: $action")
            if (SelfHealEngine.isMasterEnabled(context)) {
                SelfHealEngine.heal(context, "Boot")
                try {
                    val svc = Intent(context, WatchdogService::class.java)
                    ContextCompat.startForegroundService(context, svc)
                } catch (e: Exception) {
                    SelfHealEngine.appendLog(context, "Service start deferred: ${e.message}")
                }
            }
        }
    }
}
