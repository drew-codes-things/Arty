package app.arty.quest

import java.net.URI
import java.net.URISyntaxException

const val ADDRESS_SCHEME_PROBLEM = "Start the address with wss:// (or ws:// on your home network)."
const val ADDRESS_HOST_PROBLEM = "That doesn't look like an address."
const val ADDRESS_PLAIN_PROBLEM = "Use wss:// outside your home network, so your key isn't sent unencrypted."
const val TOKEN_PROBLEM = "Paste in the headset key from your Arty voice server."
const val CODE_PROBLEM = "Type the setup code from your Arty voice server, like K7Q9-XM3P."
const val MANUAL_KEY_PROBLEM = "This build connects with a setup code only. Make one on the voice server."
const val PAIRING_HOME_ONLY = "Only pair with a code on your home Wi-Fi: on another network, a different device could answer at this address."
const val DEFAULT_HEADSET_PORT = 8790
private const val HEADSET_PATH = "/ws/headset"
private const val PAIR_PATH = "/pair/redeem"
private const val CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
private const val CODE_LENGTH = 8

private const val SECURE_SCHEME = "wss"
private const val PLAIN_SCHEME = "ws"
private const val LOCALHOST = "localhost"
private val HOME_NAME_SUFFIXES = listOf(".local", ".lan", ".home.arpa")
private const val IPV4_PARTS = 4
private val OCTET_RANGE = 0..255
private val PRIVATE_172_RANGE = 16..31
private const val IPV6_LOOPBACK = "::1"
private const val HEX_RADIX = 16
private const val LINK_LOCAL_MASK = 0xffc0
private const val LINK_LOCAL_PREFIX = 0xfe80
private const val UNIQUE_LOCAL_MASK = 0xfe00
private const val UNIQUE_LOCAL_PREFIX = 0xfc00

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

fun headsetUrlFrom(input: String): String {
    val trimmed = input.trim().trimEnd('/')
    val hasScheme = trimmed.contains("://")
    val scheme = if (hasScheme) trimmed.substringBefore("://") else PLAIN_SCHEME
    val rest = if (hasScheme) trimmed.substringAfter("://") else trimmed
    val authority = rest.substringBefore('/').trimEnd(':')
    val path = rest.substringAfter('/', "")
    val needsPort = scheme.lowercase() != SECURE_SCHEME && !hasPort(authority)
    val withPort = if (needsPort) "$authority:$DEFAULT_HEADSET_PORT" else authority
    return "$scheme://$withPort" + if (path.isBlank()) HEADSET_PATH else "/$path"
}

private fun hasPort(authority: String): Boolean =
    if (authority.startsWith("[")) authority.substringAfter(']', "").startsWith(":") else authority.contains(':')

fun pairUrlFrom(headsetUrl: String): String {
    val uri = URI(headsetUrl)
    val scheme = if (uri.scheme.equals(SECURE_SCHEME, ignoreCase = true)) "https" else "http"
    return URI(scheme, null, uri.host, uri.port, uri.path.orEmpty().removeSuffix(HEADSET_PATH) + PAIR_PATH, null, null).toString()
}

fun pairingWarning(address: String): String? {
    if (address.isBlank()) return null
    val scheme = headsetUrlFrom(address).substringBefore("://")
    return if (scheme.equals(SECURE_SCHEME, ignoreCase = true)) null else PAIRING_HOME_ONLY
}

fun normalisedSetupCode(text: String): String = text.uppercase().filter { it.isLetterOrDigit() }

fun setupCodeProblem(text: String): String? {
    val code = normalisedSetupCode(text)
    return if (code.length == CODE_LENGTH && code.all { it in CODE_ALPHABET }) null else CODE_PROBLEM
}

fun headsetTokenProblem(token: String): String? {
    val trimmed = token.trim()
    return if (trimmed.isEmpty() || trimmed.any { it.isWhitespace() }) TOKEN_PROBLEM else null
}

fun isConfigured(url: String, token: String): Boolean =
    headsetAddressProblem(url) == null && headsetTokenProblem(token) == null

fun isHomeNetworkHost(host: String): Boolean {
    val name = host.lowercase().trimEnd('.')
    if (name.startsWith("[")) return isHomeIpv6(name.removePrefix("[").removeSuffix("]"))
    if (name == LOCALHOST || HOME_NAME_SUFFIXES.any { name.endsWith(it) }) return true
    val octets = name.split('.').map { it.toIntOrNull() ?: return false }
    if (octets.size != IPV4_PARTS || octets.any { it !in OCTET_RANGE }) return false
    val (first, second) = octets
    return first == 10 || first == 127 || (first == 172 && second in PRIVATE_172_RANGE) ||
        (first == 192 && second == 168) || (first == 169 && second == 254)
}

private fun isHomeIpv6(address: String): Boolean {
    if (address == IPV6_LOOPBACK) return true
    val firstGroup = address.substringBefore(':').toIntOrNull(HEX_RADIX) ?: return false
    return firstGroup and LINK_LOCAL_MASK == LINK_LOCAL_PREFIX || firstGroup and UNIQUE_LOCAL_MASK == UNIQUE_LOCAL_PREFIX
}

fun manualKeyProblem(allowed: Boolean): String? = if (allowed) null else MANUAL_KEY_PROBLEM
