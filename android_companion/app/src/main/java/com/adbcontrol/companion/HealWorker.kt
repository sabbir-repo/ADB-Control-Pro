package com.adbcontrol.companion

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * OS-guaranteed WorkManager periodic backup (15-minute minimum interval).
 * Survives Doze mode, OEM background throttling, and device reboots.
 */
class HealWorker(
    appContext: Context,
    params: WorkerParameters,
) : Worker(appContext, params) {

    override fun doWork(): Result {
        if (!SelfHealEngine.isMasterEnabled(applicationContext)) {
            return Result.success()
        }
        SelfHealEngine.heal(applicationContext, "WorkManager")
        if (!WatchdogService.isRunning) {
            try {
                ContextCompat.startForegroundService(
                    applicationContext,
                    Intent(applicationContext, WatchdogService::class.java),
                )
            } catch (_: Exception) {
            }
        }
        return Result.success()
    }

    companion object {
        private const val WORK_NAME = "adb_companion_periodic_heal"

        fun ensureScheduled(context: Context) {
            try {
                val req = PeriodicWorkRequestBuilder<HealWorker>(15, TimeUnit.MINUTES).build()
                WorkManager.getInstance(context.applicationContext).enqueueUniquePeriodicWork(
                    WORK_NAME,
                    ExistingPeriodicWorkPolicy.KEEP,
                    req,
                )
            } catch (_: Exception) {
            }
        }

        fun cancel(context: Context) {
            try {
                WorkManager.getInstance(context.applicationContext).cancelUniqueWork(WORK_NAME)
            } catch (_: Exception) {
            }
        }
    }
}
