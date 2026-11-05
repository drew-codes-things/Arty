package app.arty.quest

import java.net.URI
import java.net.URISyntaxException

const val ADDRESS_SCHEME_PROBLEM = "Start the address with wss:// (or ws:// on your home network)."
const val ADDRESS_HOST_PROBLEM = "That doesn't look like an address."
const val ADDRESS_PLAIN_PROBLEM = "Use wss:// outside your home network, so your key isn't sent unencrypted."
const val TOKEN_PROBLEM = "Paste in the headset key from your Arty voice server."

private const val SECURE_SCHEME = "wss"
private const val PLAIN_SCHEME = "ws"
private const val LOCALHOST = "localhost"
private val HOME_NAME_SUFFIXES = listOf(".local", ".lan", ".home.arpa")
private const val IPV4_PARTS = 4
private val OCTET_RANGE = 0..255
private val PRIVATE_172_RANGE = 16..31

fun headsetAddressProblem(address: String): String? {
    val trimmed = address.trim()
    val scheme = trimmed.substringBefore("://", "").lowercase()
    if (scheme != SECURE_SCHEME && scheme != PLAIN_SCHEME) return ADDRESS_SCHEME_PROBLEM
    val host = try {
        URI(trimmed).host.orEmpty()
    } catch (error: URISyntaxException) {
        return ADDRESS_HOST_PROBLEM
    }
    return when {
        host.isBlank() -> ADDRESS_HOST_PROBLEM
        scheme == PLAIN_SCHEME && !isHomeNetworkHost(host) -> ADDRESS_PLAIN_PROBLEM
        else -> null
    }
}

fun headsetTokenProblem(token: String): String? {
    val trimmed = token.trim()
    return if (trimmed.isEmpty() || trimmed.any { it.isWhitespace() }) TOKEN_PROBLEM else null
}

fun isConfigured(url: String, token: String): Boolean =
    headsetAddressProblem(url) == null && headsetTokenProblem(token) == null

fun isHomeNetworkHost(host: String): Boolean {
    val name = host.lowercase().trimEnd('.')
    if (name == LOCALHOST || HOME_NAME_SUFFIXES.any { name.endsWith(it) }) return true
    val octets = name.split('.').map { it.toIntOrNull() ?: return false }
    if (octets.size != IPV4_PARTS || octets.any { it !in OCTET_RANGE }) return false
    val (first, second) = octets
    return first == 10 || first == 127 || (first == 172 && second in PRIVATE_172_RANGE) ||
        (first == 192 && second == 168) || (first == 169 && second == 254)
}
