package app.arty

import android.content.Context
import android.net.Uri
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import app.arty.core.ArtyConfig
import app.arty.core.ServerAddress
import app.arty.core.StoredConnection
import java.net.InetAddress
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.security.ProviderException
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
    private const val UNREADABLE = "unreadable"
    private const val UNREADABLE_NOTICE = "The saved connection on this phone couldn't be read, so it was cleared. Enter Arty's details again or scan the setup code."
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
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val sealed = cipher.iv + cipher.doFinal(StoredConnection.toJson(config).toByteArray())
        prefs(context).edit().putString(SEALED, Base64.encodeToString(sealed, Base64.NO_WRAP)).putBoolean(UNREADABLE, false).apply()
    }

    fun takeUnreadableNotice(context: Context): String? {
        if (!prefs(context).getBoolean(UNREADABLE, false)) return null
        prefs(context).edit().putBoolean(UNREADABLE, false).apply()
        return UNREADABLE_NOTICE
    }

    private fun stored(context: Context): ArtyConfig? {
        val saved = prefs(context).getString(SEALED, null) ?: return null
        val config = try {
            unseal(saved)
        } catch (error: GeneralSecurityException) {
            return unreadable(context, error)
        } catch (error: IllegalArgumentException) {
            return unreadable(context, error)
        } catch (error: ProviderException) {
            return unreadable(context, error)
        }
        if (config == null) forgetUnreadable(context)
        return config
    }

    private fun unreadable(context: Context, error: Exception): ArtyConfig? {
        if (StoredConnection.cannotEverBeRead(error)) {
            Log.w("Arty", "the saved connection can't be read; asking again", error)
            forgetUnreadable(context)
        } else {
            Log.w("Arty", "the Keystore couldn't open the saved connection this time; keeping it", error)
        }
        return null
    }

    private fun unseal(saved: String): ArtyConfig? {
        val sealed = Base64.decode(saved, Base64.NO_WRAP)
        if (sealed.size <= IV_BYTES) return null
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed, 0, IV_BYTES))
        return StoredConnection.parse(String(cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)))
    }

    private fun forgetUnreadable(context: Context) {
        prefs(context).edit().remove(SEALED).putBoolean(UNREADABLE, true).apply()
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
        val sentHost = ServerAddress.host(normalised(address))
        return when {
            uri.scheme != "https" && uri.scheme != "http" -> "Start the address with https:// (or http:// on your home network)."
            host.isBlank() -> "That doesn't look like a web address."
            uri.userInfo != null -> "Leave the username out of the address, it can hide where the address really goes."
            sentHost == null -> "That address isn't one Arty can connect to."
            uri.scheme == "http" && !isHomeNetwork(sentHost) ->
                "Use https:// for addresses outside your home network, so your API key isn't sent unencrypted."
            else -> null
        }
    }

    /** The address as the client wants it: no trailing slash, and without a pasted "/v1" (the client adds it). */
    fun normalised(address: String): String = address.trim().trimEnd('/').removeSuffix("/v1")

    fun hostOf(address: String): String? = ServerAddress.host(normalised(address))

    private fun isHomeNetwork(host: String): Boolean {
        val name = host.lowercase()
        if (name == "localhost" || name.endsWith(".local") || name.endsWith(".lan") || name.endsWith(".home.arpa")) return true
        if (!name.all { it.isDigit() || it == '.' }) return false
        val address = try { InetAddress.getByName(name) } catch (error: Exception) { return false }
        return address.isSiteLocalAddress || address.isLoopbackAddress || address.isLinkLocalAddress
    }
}
