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
 * Lightweight zero-dependency Telegram Bot Long-Polling engine.
 * Lets the user send on-demand commands (/adb_on, /adb_off, /status, /wifi_on, /ping, /myid)
 * from Telegram to control Wireless Debugging without keeping it ON 24/7.
 */
class TelegramBotController(
    private val context: Context,
    private val onStateChanged: (() -> Unit)? = null,
) {
    @Volatile
    private var running = false
    private var worker: Thread? = null

    fun start() {
        if (running) return
        if (!SelfHealEngine.isTelegramEnabled(context)) return
        val token = SelfHealEngine.getBotToken(context)
        if (token.isBlank()) return

        running = true
        worker = Thread({ pollLoop() }, "TelegramBotPoller").apply {
            isDaemon = true
            start()
        }
        SelfHealEngine.appendLog(context, "🤖 Telegram Bot polling started")
    }

    fun stop() {
        running = false
        worker?.interrupt()
        worker = null
    }

    fun restart() {
        stop()
        start()
    }

    private fun pollLoop() {
        while (running) {
            if (!SelfHealEngine.isTelegramEnabled(context)) {
                sleepQuietly(5000)
                continue
            }
            val token = SelfHealEngine.getBotToken(context)
            if (token.isBlank()) {
                sleepQuietly(5000)
                continue
            }
            try {
                val offset = SelfHealEngine.getTelegramOffset(context)
                val urlStr = "https://api.telegram.org/bot$token/getUpdates?timeout=25&offset=$offset"
                val body = httpGet(urlStr, readTimeoutMs = 35_000)
                if (body != null) {
                    val json = JSONObject(body)
                    if (json.optBoolean("ok", false)) {
                        val arr = json.optJSONArray("result")
                        if (arr != null) {
                            for (i in 0 until arr.length()) {
                                val upd = arr.getJSONObject(i)
                                val updId = upd.optLong("update_id", 0L)
                                if (updId >= offset) {
                                    SelfHealEngine.setTelegramOffset(context, updId + 1L)
                                }
                                val msg = upd.optJSONObject("message") ?: continue
                                val chat = msg.optJSONObject("chat") ?: continue
                                val chatId = chat.optLong("id", 0L).toString()
                                val text = msg.optString("text", "").trim()
                                if (chatId != "0" && text.isNotEmpty()) {
                                    handleCommand(token, chatId, text)
                                }
                            }
                        }
                    }
                }
            } catch (_: InterruptedException) {
                break
            } catch (_: Exception) {
                // Offline or transient network error — back off gently
                sleepQuietly(6000)
            }
        }
    }

    private fun handleCommand(token: String, chatId: String, rawText: String) {
        val cmd = rawText.split(" ")[0].substringBefore("@").lowercase()

        // /myid and /start always reply with Chat ID so user can lock Allowed Chat ID easily
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

    private fun httpGet(urlStr: String, readTimeoutMs: Int): String? {
        val conn = (URL(urlStr).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 10_000
            readTimeout = readTimeoutMs
        }
        return try {
            if (conn.responseCode in 200..299) {
                BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { it.readText() }
            } else {
                null
            }
        } finally {
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
            running = false
        }
    }
}
