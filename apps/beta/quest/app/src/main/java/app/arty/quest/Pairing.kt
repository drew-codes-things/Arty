package app.arty.quest

import java.io.IOException
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONException
import org.json.JSONObject

const val PAIRING_WRONG = "That code isn't right or has run out. Make a new one on the voice server."
const val PAIRING_LOCKED = "Too many wrong codes. Wait a few minutes, then try again."
const val PAIRING_UNREACHABLE = "Couldn't reach the voice server. Check the address, and that this headset is on the same network."
const val PAIRING_BAD_REPLY = "The voice server's answer wasn't what I expected."
private const val STATUS_OK = 200
private const val STATUS_WRONG = 400
private const val STATUS_TOO_MANY = 429
private const val TIMEOUT_SECONDS = 10L
private val JSON_TYPE = "application/json".toMediaType()
private val DEVICE_ID = Regex("[0-9a-f]{1,64}")

sealed interface PairingOutcome {
    data class Key(val token: String, val device: String = "") : PairingOutcome
    data class Refused(val reason: String) : PairingOutcome
}

fun pairingOutcome(status: Int, body: String): PairingOutcome = when (status) {
    STATUS_OK -> keyFrom(body)
    STATUS_WRONG -> PairingOutcome.Refused(PAIRING_WRONG)
    STATUS_TOO_MANY -> PairingOutcome.Refused(PAIRING_LOCKED)
    else -> PairingOutcome.Refused("The voice server answered $status.")
}

fun pairingRequestBody(code: String, deviceName: String): String =
    JSONObject().put("code", code).put("device_name", deviceName).toString()

private fun keyFrom(body: String): PairingOutcome {
    val reply = try {
        JSONObject(body)
    } catch (error: JSONException) {
        return PairingOutcome.Refused(PAIRING_BAD_REPLY)
    }
    val token = reply.optString("token").trim()
    val device = reply.optString("device").trim()
    val deviceIsUsable = device.isEmpty() || DEVICE_ID.matches(device)
    if (headsetTokenProblem(token) != null || !deviceIsUsable) return PairingOutcome.Refused(PAIRING_BAD_REPLY)
    return PairingOutcome.Key(token, device)
}

object PairingClient {

    private val client = sharedClient.newBuilder().callTimeout(TIMEOUT_SECONDS, TimeUnit.SECONDS).build()

    fun redeem(pairUrl: String, code: String, deviceName: String): PairingOutcome {
        val body = pairingRequestBody(code, deviceName).toRequestBody(JSON_TYPE)
        return try {
            val request = Request.Builder().url(pairUrl).post(body).build()
            client.newCall(request).execute().use { pairingOutcome(it.code, it.body?.string().orEmpty()) }
        } catch (error: IOException) {
            PairingOutcome.Refused(PAIRING_UNREACHABLE)
        } catch (error: IllegalArgumentException) {
            PairingOutcome.Refused(PAIRING_UNREACHABLE)
        } catch (error: RuntimeException) {
            PairingOutcome.Refused(PAIRING_BAD_REPLY)
        }
    }
}
