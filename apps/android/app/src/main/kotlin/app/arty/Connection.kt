package app.arty

import android.content.Context
import android.net.Uri
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import app.arty.core.ArtyConfig
import org.json.JSONObject
import java.net.InetAddress
import java.security.GeneralSecurityException
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Where the app finds Arty's brain. The personal build has it baked in (build.gradle.kts). The shareable build
 * asks on first launch and keeps it in the app's private storage, encrypted with a key that never leaves Android's
 * Keystore. The app has backups turned off, so it stays on this phone.
 */
object Connection {
    private const val PREFS = "arty-connection"
    private const val SEALED = "sealed"
    private const val KEY_ALIAS = "arty-connection"
    private const val KEYSTORE = "AndroidKeyStore"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val TAG_BITS = 128
    private const val IV_BYTES = 12

    /** True when this build asks for the brain's details on the phone instead of having them built in. */
    val setUpOnPhone: Boolean get() = BuildConfig.SETUP_ON_PHONE

    fun current(context: Context): ArtyConfig? =
        if (setUpOnPhone) stored(context)
        else ArtyConfig(BuildConfig.ARTY_BASE_URL, BuildConfig.ARTY_API_KEY, BuildConfig.CF_ACCESS_CLIENT_ID, BuildConfig.CF_ACCESS_CLIENT_SECRET)

    fun isSet(context: Context): Boolean = current(context) != null

    fun save(context: Context, config: ArtyConfig) {
        val json = JSONObject()
            .put("baseUrl", config.baseUrl).put("apiKey", config.apiKey)
            .put("accessClientId", config.accessClientId).put("accessClientSecret", config.accessClientSecret)
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val sealed = cipher.iv + cipher.doFinal(json.toString().toByteArray())
        prefs(context).edit().putString(SEALED, Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
    }

    private fun stored(context: Context): ArtyConfig? {
        val sealed = prefs(context).getString(SEALED, null)?.let { Base64.decode(it, Base64.NO_WRAP) } ?: return null
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed, 0, IV_BYTES))
            val json = JSONObject(String(cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)))
            ArtyConfig(json.getString("baseUrl"), json.getString("apiKey"), json.optString("accessClientId"), json.optString("accessClientSecret"))
        } catch (error: GeneralSecurityException) {
            // The Keystore key is gone (e.g. the screen lock was reset): ask for the details again.
            Log.w("Arty", "couldn't read the saved connection; asking again", error)
            null
        }
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val spec = KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build()
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE).apply { init(spec) }.generateKey()
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /**
     * Why [address] can't be used, or null when it's fine. Plain http is only allowed to the home network
     * (private addresses and .local names), since the API key would otherwise cross the internet unencrypted.
     */
    fun addressProblem(address: String): String? {
        val uri = Uri.parse(address.trim())
        val host = uri.host.orEmpty()
        return when {
            uri.scheme != "https" && uri.scheme != "http" -> "Start the address with https:// (or http:// on your home network)."
            host.isBlank() -> "That doesn't look like a web address."
            uri.scheme == "http" && !isHomeNetwork(host) ->
                "Use https:// for addresses outside your home network, so your API key isn't sent unencrypted."
            else -> null
        }
    }

    /** The address as the client wants it: no trailing slash, and without a pasted "/v1" (the client adds it). */
    fun normalised(address: String): String = address.trim().trimEnd('/').removeSuffix("/v1")

    private fun isHomeNetwork(host: String): Boolean {
        if (host == "localhost" || host.endsWith(".local") || host.endsWith(".lan") || host.endsWith(".home.arpa")) return true
        if (!host.all { it.isDigit() || it == '.' }) return false // a name: can't tell without looking it up
        val address = try { InetAddress.getByName(host) } catch (error: Exception) { return false }
        return address.isSiteLocalAddress || address.isLoopbackAddress || address.isLinkLocalAddress
    }
}
