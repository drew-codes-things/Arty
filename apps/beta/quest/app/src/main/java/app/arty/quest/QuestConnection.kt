package app.arty.quest

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import java.security.GeneralSecurityException
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONException
import org.json.JSONObject

data class HeadsetSettings(val url: String, val token: String)

object QuestConnection {
    private const val PREFS = "arty-headset-connection"
    private const val SEALED = "sealed"
    private const val KEY_ALIAS = "arty-headset-connection"
    private const val KEYSTORE = "AndroidKeyStore"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val TAG_BITS = 128
    private const val IV_BYTES = 12
    private const val URL_FIELD = "url"
    private const val TOKEN_FIELD = "token"
    private const val STORAGE_TAG = "ArtyQuest"

    fun current(context: Context): HeadsetSettings? = stored(context) ?: builtIn()

    fun save(context: Context, settings: HeadsetSettings) {
        val json = JSONObject().put(URL_FIELD, settings.url).put(TOKEN_FIELD, settings.token)
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val sealed = cipher.iv + cipher.doFinal(json.toString().toByteArray())
        prefs(context).edit().putString(SEALED, Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
    }

    private fun builtIn(): HeadsetSettings? =
        HeadsetSettings(BuildConfig.HEADSET_URL, BuildConfig.HEADSET_TOKEN).takeIf { isConfigured(it.url, it.token) }

    private fun stored(context: Context): HeadsetSettings? {
        val sealed = prefs(context).getString(SEALED, null)?.let { Base64.decode(it, Base64.NO_WRAP) } ?: return null
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed, 0, IV_BYTES))
            val json = JSONObject(String(cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)))
            HeadsetSettings(json.getString(URL_FIELD), json.getString(TOKEN_FIELD)).takeIf { isConfigured(it.url, it.token) }
        } catch (error: GeneralSecurityException) {
            Log.w(STORAGE_TAG, "couldn't read the saved headset connection; asking again", error)
            null
        } catch (error: JSONException) {
            Log.w(STORAGE_TAG, "the saved headset connection is damaged; asking again", error)
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
}
