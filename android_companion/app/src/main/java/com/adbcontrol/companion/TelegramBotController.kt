package com.adbcontrol.companion

import android.content.Context
import android.os.Build
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Rock-solid, single-instance Telegram Bot Long-Polling engine.
 * - Singleton lock prevents duplicate `getUpdates` (eliminates HTTP 409 Conflict).
 * - Explicitly disconnects active HttpURLConnection on stop/restart.
 * - Exposes `pollOnceShort(context)` so HealAlarmReceiver can still process commands
 *   even if OEM (Tecno HiOS) kills the foreground service after swipe-away.
 */
object TelegramBotController {
    @Volatile
    private var running = false
    @Volatile
    private var worker: Thread? = null
    @Volatile
    private var activeConn: HttpURLConnection? = null
    private val lifecycleLock = Any()
    private val pollLock = Any()

    val isPolling: Boolean
        get() = running && worker?.isAlive == true

    fun start(context: Context, onStateChanged: (() -> Unit)? = null) {
        val appCtx = context.applicationContext
        synchronized(lifecycleLock) {
            if (isPolling) return
            if (!SelfHealEngine.isTelegramEnabled(appCtx)) return
            val token = SelfHealEngine.getBotToken(appCtx)
            if (token.isBlank()) return

            running = true
            worker = Thread({ pollLoop(appCtx, onStateChanged) }, "TelegramBotPoller").apply {
                isDaemon = true
                start()
            }
            SelfHealEngine.appendLog(appCtx, "🤖 Telegram Bot polling started")
        }
    }

    fun stop() {
        synchronized(lifecycleLock) {
            running = false
            try {
                activeConn?.disconnect()
            } catch (_: Exception) {
            }
            activeConn = null
            worker?.interrupt()
            worker = null
        }
    }

    fun restart(context: Context, onStateChanged: (() -> Unit)? = null) {
        stop()
        try {
            Thread.sleep(250)
        } catch (_: InterruptedException) {
        }
        start(context, onStateChanged)
    }

    /** Quick test via getMe so user can verify bot token & internet connectivity. */
    fun testConnection(context: Context): String {
        val token = SelfHealEngine.getBotToken(context)
        if (token.isBlank()) return "❌ Bot Token খালি।"
        return try {
            val (code, body) = httpGet("https://api.telegram.org/bot$token/getMe", readTimeoutMs = 10_000, trackConn = false)
            if (code in 200..299 && body != null) {
                val json = JSONObject(body)
                val user = json.optJSONObject("result")?.optString("username", "bot") ?: "bot"
                val msg = "✅ Bot Connected: @$user"
                SelfHealEngine.appendLog(context, msg)
                msg
            } else {
                val msg = "❌ Telegram HTTP $code"
                SelfHealEngine.appendLog(context, msg)
                msg
            }
        } catch (e: Exception) {
            val msg = "❌ Network Error: ${e.message}"
            SelfHealEngine.appendLog(context, msg)
            msg
        }
    }

    /** Non-blocking short poll (`timeout=0`) called by AlarmReceiver if WatchdogService was killed. */
    fun pollOnceShort(context: Context): Int {
        val appCtx = context.applicationContext
        if (!SelfHealEngine.isTelegramEnabled(appCtx)) return 0
        val token = SelfHealEngine.getBotToken(appCtx)
        if (token.isBlank()) return 0
        synchronized(pollLock) {
            return try {
                val offset = SelfHealEngine.getTelegramOffset(appCtx)
                val (code, body) = httpGet(
                    "https://api.telegram.org/bot$token/getUpdates?timeout=0&offset=$offset",
                    readTimeoutMs = 10_000,
                    trackConn = false,
                )
                if (code in 200..299 && body != null) {
                    processUpdates(appCtx, token, body, null)
                } else {
                    0
                }
            } catch (_: Exception) {
                0
            }
        }
    }

    private fun pollLoop(context: Context, onStateChanged: (() -> Unit)?) {
        while (running) {
            if (!SelfHealEngine.isTelegramEnabled(context)) {
                sleepQuietly(4000)
                continue
            }
            val token = SelfHealEngine.getBotToken(context)
            if (token.isBlank()) {
                sleepQuietly(4000)
                continue
            }
            try {
                val offset = SelfHealEngine.getTelegramOffset(context)
                val urlStr = "https://api.telegram.org/bot$token/getUpdates?timeout=15&offset=$offset"
                val (code, body) = httpGet(urlStr, readTimeoutMs = 22_000, trackConn = true)
                if (!running) break

                if (code in 200..299 && body != null) {
                    synchronized(pollLock) {
                        processUpdates(context, token, body, onStateChanged)
                    }
                } else if (code == 409) {
                    // Another poller briefly existed; back off cleanly
                    SelfHealEngine.appendLog(context, "⚠️ Telegram 409 Conflict — retrying in 5s")
                    sleepQuietly(5000)
                } else if (code > 0) {
                    SelfHealEngine.appendLog(context, "⚠️ Telegram HTTP $code — retrying in 5s")
                    sleepQuietly(5000)
                } else {
                    sleepQuietly(4000)
                }
            } catch (_: InterruptedException) {
                break
            } catch (_: Exception) {
                if (running) sleepQuietly(5000)
            }
        }
    }

    private fun processUpdates(
        context: Context,
        token: String,
        body: String,
        onStateChanged: (() -> Unit)?,
    ): Int {
        var handled = 0
        val json = JSONObject(body)
        if (!json.optBoolean("ok", false)) return 0
        val arr = json.optJSONArray("result") ?: return 0
        var offset = SelfHealEngine.getTelegramOffset(context)

        for (i in 0 until arr.length()) {
            val upd = arr.getJSONObject(i)
            val updId = upd.optLong("update_id", 0L)
            if (updId >= offset) {
                offset = updId + 1L
                SelfHealEngine.setTelegramOffset(context, offset)
            }
            val msg = upd.optJSONObject("message") ?: continue
            val chat = msg.optJSONObject("chat") ?: continue
            val chatId = chat.optLong("id", 0L).toString()
            val text = msg.optString("text", "").trim()
            if (chatId != "0" && text.isNotEmpty()) {
                handleCommand(context, token, chatId, text, onStateChanged)
                handled++
            }
        }
        return handled
    }

    private fun handleCommand(
        context: Context,
        token: String,
        chatId: String,
        rawText: String,
        onStateChanged: (() -> Unit)?,
    ) {
        val cmd = rawText.split(" ")[0].substringBefore("@").lowercase()

        if (cmd == "/myid" || cmd == "/start") {
            sendMessage(
                token,
                chatId,
                "👋 ADB Control Companion Ready!\n" +
                    "🆔 Your Chat ID: $chatId\n\n" +
                    "Commands:\n" +
                    "• /adb_on — Wireless Debugging ON\n" +
                    "• /adb_off — Wireless Debugging OFF\n" +
                    "• /status — Phone & ADB Status\n" +
                    "• /wifi_on — Try Wi-Fi ON\n" +
                    "• /ping — Online Check",
            )
            return
        }

        val allowed = SelfHealEngine.getAllowedChatId(context)
        if (allowed.isNotEmpty() && allowed != chatId) {
            SelfHealEngine.appendLog(context, "🚫 Ignored unauthorized TG chat: $chatId")
            return
        }

        val reply = when (cmd) {
            "/adb_on", "/on" -> {
                val r = SelfHealEngine.setAdbWifi(context, true, "TG")
                onStateChanged?.invoke()
                r
            }
            "/adb_off", "/off" -> {
                val r = SelfHealEngine.setAdbWifi(context, false, "TG")
                onStateChanged?.invoke()
                r
            }
            "/wifi_on" -> {
                val r = SelfHealEngine.tryEnableWifi(context, "TG")
                onStateChanged?.invoke()
                r
            }
            "/status" -> {
                val bat = SelfHealEngine.getBatteryLevel(context)
                val wifiConn = SelfHealEngine.isWifiConnected(context)
                val ip = SelfHealEngine.getWifiIpAddress(context)
                val adbOn = SelfHealEngine.isAdbWifiEnabled(context)
                val perm = SelfHealEngine.hasSecureSettingsPermission(context)
                "📱 ${Build.MANUFACTURER} ${Build.MODEL} (Android ${Build.VERSION.RELEASE})\n" +
                    "🔋 Battery: ${if (bat >= 0) "$bat%" else "N/A"}\n" +
                    "📶 Wi-Fi: ${if (wifiConn) "Connected ($ip)" else "Not Connected"}\n" +
                    "📡 Wireless Debugging: ${if (adbOn) "🟢 ON" else "🔴 OFF"}\n" +
                    "🔐 Permission: ${if (perm) "✅ Granted" else "❌ Missing"}"
            }
            "/ping" -> "🏓 Pong! ${Build.MODEL} online."
            "/help" ->
                "🤖 ADB Companion Commands:\n" +
                    "• /adb_on — Wireless Debugging ON\n" +
                    "• /adb_off — Wireless Debugging OFF\n" +
                    "• /status — Live Device Status\n" +
                    "• /wifi_on — Try Wi-Fi ON\n" +
                    "• /ping — Online Check\n" +
                    "• /myid — Show Your Chat ID"
            else -> return
        }
        sendMessage(token, chatId, reply)
    }

    private fun httpGet(urlStr: String, readTimeoutMs: Int, trackConn: Boolean): Pair<Int, String?> {
        val conn = (URL(urlStr).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 10_000
            readTimeout = readTimeoutMs
        }
        if (trackConn) activeConn = conn
        return try {
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.let { BufferedReader(InputStreamReader(it, Charsets.UTF_8)).use { r -> r.readText() } }
            code to body
        } finally {
            if (trackConn && activeConn === conn) activeConn = null
            conn.disconnect()
        }
    }

    private fun sendMessage(token: String, chatId: String, text: String) {
        try {
            val conn = (URL("https://api.telegram.org/bot$token/sendMessage").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = 10_000
                readTimeout = 10_000
                setRequestProperty("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
            }
            val payload = "chat_id=${URLEncoder.encode(chatId, "UTF-8")}&text=${URLEncoder.encode(text, "UTF-8")}"
            OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { it.write(payload) }
            conn.responseCode
            conn.disconnect()
        } catch (_: Exception) {
        }
    }

    private fun sleepQuietly(ms: Long) {
        try {
            Thread.sleep(ms)
        } catch (_: InterruptedException) {
            // exit or recheck loop condition
        }
    }
}
