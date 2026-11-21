package app.arty.core

import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.math.BigInteger
import java.net.HttpURLConnection
import java.net.URI
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

open class PhoneLinkException(message: String) : Exception(message)

class LinkUnreachable(message: String) : PhoneLinkException(message)

object LinkMath {
    const val CONTEXT = "arty-phone-link-v1"
    const val PHONE = "app"
    const val PHONE_SEALING = "app-sealed-1"
    const val ARTY = "arty"
    const val DEVICE_KEY_PREFIX = "arty_dev_"
    private val PRIME = BigInteger(
        "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD" +
            "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED" +
            "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F" +
            "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B" +
            "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510" +
            "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
        16,
    )
    private val GENERATOR = BigInteger.TWO
    private val ORDER = PRIME.subtract(BigInteger.ONE).shiftRight(1)
    private const val ELEMENT_BYTES = 256
    private const val SECRET_BITS = ELEMENT_BYTES * 8 + 256
    private const val HASH_BLOCKS_PER_ELEMENT = 5
    private val SHARE_FORMAT = Regex("[0-9a-f]{${ELEMENT_BYTES * 2}}")
    private const val SHA512 = "SHA-512"
    private const val SHA256 = "SHA-256"
    private const val HMAC = "HmacSHA256"
    private const val SEAL_NONCE_BYTES = 16
    private const val SEAL_TAG_BYTES = 32
    private const val SEAL_BLOCK_BYTES = 32
    private val SEALED_FORMAT = Regex("(?:[0-9a-f]{2})+")
    const val SEALED_ACCESS_VERSION = 1
    const val CHANGED_ON_THE_WAY =
        "Arty's answer to the link was changed on the way, so nothing was saved. Show a new code and scan again."

    val blinds: Map<String, BigInteger> by lazy { mapOf(PHONE to hashedElement("M"), ARTY to hashedElement("N")) }

    private fun bytes(vararg parts: Any): ByteArray = parts.fold(ByteArray(0)) { joined, part ->
        joined + when (part) {
            is String -> part.toByteArray(Charsets.UTF_8)
            is ByteArray -> part
            else -> throw IllegalArgumentException("only text and bytes are joined")
        }
    }

    private fun digest(algorithm: String, input: ByteArray): ByteArray = MessageDigest.getInstance(algorithm).digest(input)

    private fun hmac(key: ByteArray, message: ByteArray): ByteArray =
        Mac.getInstance(HMAC).apply { init(SecretKeySpec(key, HMAC)) }.doFinal(message)

    private fun hex(input: ByteArray): String = input.joinToString("") { "%02x".format(it) }

    private fun bytesOfHex(text: String): ByteArray = ByteArray(text.length / 2) { text.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun elementBytes(element: BigInteger): ByteArray {
        val magnitude = element.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
        return ByteArray(ELEMENT_BYTES - magnitude.size) + magnitude
    }

    private fun hashedElement(label: String): BigInteger {
        val blocks = (0 until HASH_BLOCKS_PER_ELEMENT).fold(ByteArray(0)) { joined, index ->
            joined + digest(SHA512, bytes(CONTEXT, "|", label, byteArrayOf(index.toByte())))
        }
        return BigInteger(1, blocks).mod(PRIME).modPow(BigInteger.TWO, PRIME)
    }

    fun codeScalar(code: String): BigInteger = BigInteger(1, digest(SHA512, bytes(CONTEXT, "|code|", code))).mod(ORDER)

    fun newSecret(random: SecureRandom = SecureRandom()): BigInteger =
        BigInteger(SECRET_BITS, random).mod(ORDER.subtract(BigInteger.ONE)).add(BigInteger.ONE)

    fun shareHex(secret: BigInteger, code: String, role: String): String {
        val blind = blinds.getValue(role)
        val element = GENERATOR.modPow(secret, PRIME).multiply(blind.modPow(codeScalar(code), PRIME)).mod(PRIME)
        return hex(elementBytes(element))
    }

    fun element(share: String): BigInteger {
        if (!SHARE_FORMAT.matches(share)) throw PhoneLinkException("Arty's answer to the link wasn't the right shape.")
        val element = BigInteger(share, 16)
        val isInGroup = element > BigInteger.ONE && element < PRIME.subtract(BigInteger.ONE) && element.modPow(ORDER, PRIME) == BigInteger.ONE
        if (!isInGroup) throw PhoneLinkException("Arty's answer to the link wasn't a value this link can use.")
        return element
    }

    fun sharedKey(secret: BigInteger, code: String, role: String, phoneShare: String, artyShare: String): ByteArray {
        val theirs = element(if (role == PHONE) artyShare else phoneShare)
        element(if (role == PHONE) phoneShare else artyShare)
        val scalar = codeScalar(code)
        val otherBlind = blinds.getValue(if (role == PHONE) ARTY else PHONE)
        val unblinded = theirs.multiply(otherBlind.modPow(ORDER.subtract(scalar), PRIME)).mod(PRIME)
        val shared = unblinded.modPow(secret, PRIME)
        val transcript = bytes(CONTEXT, bytesOfHex(phoneShare), bytesOfHex(artyShare), elementBytes(shared), elementBytes(scalar))
        return digest(SHA256, transcript)
    }

    fun proof(key: ByteArray, role: String): String = hex(hmac(key, bytes(CONTEXT, "|proof|", role)))

    private fun keystream(key: ByteArray, nonce: ByteArray, length: Int): ByteArray {
        val streamKey = hmac(key, bytes(CONTEXT, "|seal-stream"))
        val blocks = (length + SEAL_BLOCK_BYTES - 1) / SEAL_BLOCK_BYTES
        return (0 until blocks).fold(ByteArray(0)) { joined, block ->
            joined + hmac(streamKey, nonce + java.nio.ByteBuffer.allocate(Int.SIZE_BYTES).putInt(block).array())
        }.copyOf(length)
    }

    fun openSealed(key: ByteArray, sealed: String): ByteArray {
        if (!SEALED_FORMAT.matches(sealed)) throw PhoneLinkException(CHANGED_ON_THE_WAY)
        val raw = bytesOfHex(sealed)
        if (raw.size < SEAL_NONCE_BYTES + SEAL_TAG_BYTES) throw PhoneLinkException(CHANGED_ON_THE_WAY)
        val nonce = raw.copyOfRange(0, SEAL_NONCE_BYTES)
        val body = raw.copyOfRange(SEAL_NONCE_BYTES, raw.size - SEAL_TAG_BYTES)
        val tag = raw.copyOfRange(raw.size - SEAL_TAG_BYTES, raw.size)
        val expected = hmac(hmac(key, bytes(CONTEXT, "|seal-tag")), nonce + body)
        if (!MessageDigest.isEqual(tag, expected)) throw PhoneLinkException(CHANGED_ON_THE_WAY)
        val stream = keystream(key, nonce, body.size)
        return ByteArray(body.size) { (body[it].toInt() xor stream[it].toInt()).toByte() }
    }

    fun deviceKey(key: ByteArray): String =
        DEVICE_KEY_PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(hmac(key, bytes(CONTEXT, "|token")))
}

data class LinkCode(val address: String, val offer: String, val code: String, val homeAddress: String = "") {
    companion object {
        const val FORMAT_VERSION = 2
        private val OFFER_FORMAT = Regex("[A-Za-z0-9_-]{8,64}")
        private val CODE_FORMAT = Regex("[0-9a-f]{32}")

        fun isWhole(offer: String, code: String): Boolean = OFFER_FORMAT.matches(offer) && CODE_FORMAT.matches(code)

        fun from(json: JSONObject): LinkCode {
            val link = LinkCode(
                SetupCode.field(json, "url"), SetupCode.field(json, "offer"), SetupCode.field(json, "code"), SetupCode.field(json, "home"),
            )
            when {
                link.address.isEmpty() -> throw SetupCodeException("The link code has no address in it.")
                SetupCode.hasUsername(link.address) || SetupCode.hasUsername(link.homeAddress) -> throw SetupCodeException(
                    "The link code's address has a username in it, which can hide where it really goes, so it was refused.",
                )
                !isWhole(link.offer, link.code) -> throw SetupCodeException("This link code is damaged. Show a new one in Arty Desktop.")
            }
            return link
        }
    }
}

sealed interface ScannedCode {
    data class Keys(val code: SetupCode) : ScannedCode
    data class Link(val code: LinkCode) : ScannedCode

    companion object {
        fun parse(scanned: String): ScannedCode {
            val json = SetupCode.readObject(scanned)
            if (SetupCode.wholeNumber(json.opt("arty")) == LinkCode.FORMAT_VERSION) return Link(LinkCode.from(json))
            return Keys(SetupCode.parse(scanned))
        }
    }
}

fun interface LinkTransport {
    fun post(path: String, body: JSONObject): JSONObject
}

class FallbackTransport(private val first: LinkTransport, private val second: LinkTransport?) : LinkTransport {
    private var chosen: LinkTransport? = null

    override fun post(path: String, body: JSONObject): JSONObject {
        chosen?.let { return it.post(path, body) }
        return try {
            first.post(path, body).also { chosen = first }
        } catch (unreachable: LinkUnreachable) {
            val other = second ?: throw unreachable
            other.post(path, body).also { chosen = other }
        }
    }
}

class PhoneLink(private val transport: LinkTransport) {
    fun link(code: LinkCode, name: String, secret: BigInteger = LinkMath.newSecret()): ArtyConfig {
        val myShare = LinkMath.shareHex(secret, code.code, LinkMath.PHONE)
        val begun = transport.post(BEGIN_PATH, JSONObject().put("offer", code.offer).put("name", name).put("share", myShare))
        val key = LinkMath.sharedKey(secret, code.code, LinkMath.PHONE, myShare, begun.optString("share"))
        val expected = LinkMath.proof(key, LinkMath.ARTY).toByteArray()
        if (!MessageDigest.isEqual(begun.optString("proof").toByteArray(), expected)) throw PhoneLinkException(NOT_THIS_ARTY)
        val finished = transport.post(
            FINISH_PATH,
            JSONObject().put("offer", code.offer).put("session", begun.optString("session")).put("proof", LinkMath.proof(key, LinkMath.PHONE_SEALING))
                .put("seal", LinkMath.SEALED_ACCESS_VERSION),
        )
        val access = accessFrom(finished, key)
        return ArtyConfig(code.address, LinkMath.deviceKey(key), access?.optString("id").orEmpty(), access?.optString("secret").orEmpty())
    }

    private fun accessFrom(finished: JSONObject, key: ByteArray): JSONObject? {
        if (!finished.has("sealed_access")) {
            if (finished.has("access") && !finished.isNull("access")) throw PhoneLinkException(LinkMath.CHANGED_ON_THE_WAY)
            return null
        }
        val opened = LinkMath.openSealed(key, finished.optString("sealed_access")).toString(Charsets.UTF_8)
        return try {
            JSONObject(opened)
        } catch (error: JSONException) {
            throw PhoneLinkException(LinkMath.CHANGED_ON_THE_WAY)
        }
    }

    companion object {
        const val BEGIN_PATH = "/link/phone/begin"
        const val FINISH_PATH = "/link/phone/finish"
        const val NOT_THIS_ARTY =
            "That answer didn't come from the Arty that showed the code, so nothing was saved. Show a new code and scan again."
    }
}

class HttpLinkTransport(
    private val address: String,
    private val connectTimeoutMs: Int = 15_000,
    private val readTimeoutMs: Int = 30_000,
) : LinkTransport {
    override fun post(path: String, body: JSONObject): JSONObject {
        val connection = try {
            URI(address.trimEnd('/') + path).toURL().openConnection() as HttpURLConnection
        } catch (error: IllegalArgumentException) {
            throw PhoneLinkException("The link code's address can't be used: $address")
        } catch (error: java.net.URISyntaxException) {
            throw PhoneLinkException("The link code's address can't be used: $address")
        }
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = connectTimeoutMs
            connection.readTimeout = readTimeoutMs
            connection.instanceFollowRedirects = false
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
            val status = connection.responseCode
            val text = (if (status >= ERROR_STATUS) connection.errorStream else connection.inputStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (status != OK_STATUS) throw brainMessage(text)?.let(::PhoneLinkException) ?: LinkUnreachable(blockedMessage(status))
            return JSONObject(text)
        } catch (error: JSONException) {
            throw LinkUnreachable(blockedMessage(-1))
        } catch (error: IOException) {
            throw LinkUnreachable("Can't reach Arty at $address (${error.message ?: error.javaClass.simpleName}). Check the phone's connection.")
        } finally {
            connection.disconnect()
        }
    }

    private fun brainMessage(text: String): String? = try {
        JSONObject(text).optJSONObject("error")?.optString("message")?.takeIf { it.isNotBlank() }
    } catch (error: JSONException) {
        null
    }

    private fun blockedMessage(status: Int): String =
        "Arty at $address didn't answer the link (${if (status > 0) "HTTP $status" else "not Arty's reply"}). " +
            "Away from home, the link page needs to be open in Cloudflare (docs/connecting-a-phone.md), or link on your home Wi-Fi."

    companion object {
        private const val OK_STATUS = 200
        private const val ERROR_STATUS = 400
    }
}
