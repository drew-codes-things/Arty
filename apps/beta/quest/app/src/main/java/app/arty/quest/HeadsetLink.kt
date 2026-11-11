package app.arty.quest

import android.os.Handler
import android.os.Looper
import android.util.Log
import java.io.IOException
import java.net.URISyntaxException
import java.util.concurrent.TimeUnit
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString

private const val LINK_TAG = "ArtyLink"
private const val FIRST_RETRY_MS = 1_000L
private const val LONGEST_RETRY_MS = 30_000L
private const val PING_SECONDS = 20L
private const val NORMAL_CLOSE = 1000

fun reconnectDelayMs(failures: Int): Long {
    val doubled = FIRST_RETRY_MS shl failures.coerceIn(0, 5)
    return doubled.coerceAtMost(LONGEST_RETRY_MS)
}

const val MAX_FAILED_ATTEMPTS = 20
const val LINK_GAVE_UP = "Arty couldn't be reached for several minutes, so the headset stopped listening. Open Arty again to retry."

fun shouldStopRetrying(failures: Int): Boolean = failures >= MAX_FAILED_ATTEMPTS

const val KEY_REFUSED = "the headset key was refused"
private const val UNAUTHORISED = 401

fun needsNewKey(reason: String): Boolean = reason == KEY_REFUSED

internal val sharedClient: OkHttpClient = OkHttpClient.Builder().pingInterval(PING_SECONDS, TimeUnit.SECONDS).build()

interface HeadsetEvents {
    fun connected()
    fun disconnected(reason: String)
    fun message(message: ServerMessage)
    fun audio(wav: ByteArray)
}

class HeadsetLink(private val url: String, private val token: String, private val device: String, private val events: HeadsetEvents) {

    private val client = sharedClient
    private val main = Handler(Looper.getMainLooper())
    @Volatile
    private var socket: WebSocket? = null
    private var listener: Listener? = null
    private var failures = 0
    private var wanted = false

    fun open() {
        wanted = true
        connect()
    }

    fun close() {
        wanted = false
        main.removeCallbacksAndMessages(null)
        listener = null
        socket?.close(NORMAL_CLOSE, "headset app closed")
        socket = null
    }

    fun sendText(text: String): Boolean = socket?.send(text) ?: false

    fun sendAudio(pcm: ByteArray): Boolean = socket?.send(pcm.toByteString()) ?: false

    private fun connect() {
        if (!wanted) return
        val current = Listener()
        listener = current
        if (needsIdentityCheck(url)) checkIdentityThenOpen(current) else openSocket(current)
    }

    private fun openSocket(current: Listener) {
        val request = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
        socket = client.newWebSocket(request, current)
    }

    private fun checkIdentityThenOpen(current: Listener) {
        val nonce = newIdentityNonce()
        val request = try {
            Request.Builder().url(identityUrlFrom(url, nonce, device)).build()
        } catch (error: URISyntaxException) {
            retryLater(IDENTITY_REFUSED)
            return
        } catch (error: IllegalArgumentException) {
            retryLater(IDENTITY_REFUSED)
            return
        }
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                main.post { if (current === listener) retryLater(e.message ?: e.javaClass.simpleName) }
            }

            override fun onResponse(call: Call, response: Response) {
                val proven = try {
                    response.use { it.isSuccessful && identityProven(token, nonce, it.body?.string().orEmpty(), device) }
                } catch (error: IOException) {
                    false
                }
                main.post {
                    if (current !== listener) return@post
                    if (proven) openSocket(current) else retryLater(IDENTITY_REFUSED)
                }
            }
        })
    }

    private fun retryLater(reason: String) {
        socket = null
        listener = null
        if (wanted && !needsNewKey(reason) && shouldStopRetrying(failures)) {
            wanted = false
            Log.w(LINK_TAG, "lost Arty ($reason) $failures times in a row; giving up")
            events.disconnected(LINK_GAVE_UP)
            return
        }
        events.disconnected(reason)
        if (!wanted) return
        val delay = reconnectDelayMs(failures++)
        Log.w(LINK_TAG, "lost Arty ($reason), trying again in ${delay / 1000}s")
        main.postDelayed({ connect() }, delay)
    }

    private inner class Listener : WebSocketListener() {
        private fun onMain(action: () -> Unit) {
            main.post { if (this === listener) action() }
        }

        override fun onOpen(webSocket: WebSocket, response: Response) = onMain {
            failures = 0
            events.connected()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val parsed = HeadsetProtocol.parse(text) ?: return
            onMain { events.message(parsed) }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            val wav = bytes.toByteArray()
            onMain { events.audio(wav) }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = onMain {
            retryLater("closed ($code)")
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            val reason = if (response?.code == UNAUTHORISED) KEY_REFUSED else (t.message ?: t.javaClass.simpleName)
            onMain { retryLater(reason) }
        }
    }
}
