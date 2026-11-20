package com.adbcontrol.companion

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import androidx.core.content.ContextCompat

class HealAlarmReceiver : BroadcastReceiver() {

    companion object {
        private const val ACTION_HEAL_TICK = "com.adbcontrol.companion.ACTION_HEAL_TICK"
        private const val REQ_CODE = 7701

        fun scheduleNext(context: Context, delayMs: Long = 60_000L) {
            if (!SelfHealEngine.isMasterEnabled(context)) return
            val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
            val intent = Intent(context, HealAlarmReceiver::class.java).apply {
                action = ACTION_HEAL_TICK
            }
            val pi = PendingIntent.getBroadcast(
                context,
                REQ_CODE,
                intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val triggerAt = SystemClock.elapsedRealtime() + delayMs
            try {
                am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pi)
            } catch (_: Exception) {
                am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pi)
            }
        }

        fun cancel(context: Context) {
            val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
            val intent = Intent(context, HealAlarmReceiver::class.java).apply {
                action = ACTION_HEAL_TICK
            }
            val pi = PendingIntent.getBroadcast(
                context,
                REQ_CODE,
                intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_NO_CREATE,
            )
            if (pi != null) am.cancel(pi)
        }
    }

    override fun onReceive(context: Context, intent: Intent?) {
        if (!SelfHealEngine.isMasterEnabled(context)) return
        SelfHealEngine.heal(context, "AlarmWatchdog")
        if (!WatchdogService.isRunning) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, WatchdogService::class.java))
            } catch (e: Exception) {
                SelfHealEngine.appendLog(context, "Revive service skipped: ${e.message}")
            }
        }
        scheduleNext(context, 60_000L)
    }
}
