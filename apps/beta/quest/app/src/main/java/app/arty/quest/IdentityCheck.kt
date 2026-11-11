package app.arty.quest

import java.net.URI
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import org.json.JSONException
import org.json.JSONObject

const val IDENTITY_REFUSED = "The voice server at this address couldn't prove it's yours, so the headset key wasn't sent. On another network, use the wss:// address."
private const val IDENTITY_PATH = "/identity"
private const val HEADSET_SOCKET_PATH = "/ws/headset"
private const val NONCE_BYTES = 16
private const val HMAC = "HmacSHA256"
private const val SHA256 = "SHA-256"
private const val PLAIN_SOCKET = "ws"
private val random = SecureRandom()

fun needsIdentityCheck(headsetUrl: String): Boolean =
    headsetUrl.trim().substringBefore("://", "").equals(PLAIN_SOCKET, ignoreCase = true)

fun identityUrlFrom(headsetUrl: String, nonce: String, device: String = ""): String {
    val uri = URI(headsetUrl)
    val path = uri.path.orEmpty().removeSuffix(HEADSET_SOCKET_PATH) + IDENTITY_PATH
    val query = if (device.isEmpty()) "nonce=$nonce" else "nonce=$nonce&device=$device"
    return URI("http", null, uri.host, uri.port, path, query, null).toString()
}

fun newIdentityNonce(): String = ByteArray(NONCE_BYTES).also { random.nextBytes(it) }.toHex()

private fun proofKey(token: String, device: String): ByteArray =
    if (device.isEmpty()) token.toByteArray() else MessageDigest.getInstance(SHA256).digest(token.toByteArray()).toHex().toByteArray()

fun identityProven(token: String, nonce: String, body: String, device: String = ""): Boolean {
    if (token.isEmpty()) return false
    val given = try {
        JSONObject(body).optString("proof")
    } catch (error: JSONException) {
        return false
    }
    val mac = Mac.getInstance(HMAC).apply { init(SecretKeySpec(proofKey(token, device), HMAC)) }
    val expected = mac.doFinal("arty-identity:$nonce".toByteArray()).toHex()
    return MessageDigest.isEqual(expected.toByteArray(), given.lowercase().toByteArray())
}

private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
