package app.arty.quest

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class HeadsetSettings(val url: String, val token: String, val device: String = "")

object QuestConnection {
    private const val PREFS = "arty-headset-connection"
    private const val SEALED = "sealed"
    private const val KEY_ALIAS = "arty-headset-connection"
    private const val KEYSTORE = "AndroidKeyStore"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val TAG_BITS = 128
    private const val STORAGE_TAG = "ArtyQuest"

    fun current(context: Context): HeadsetSettings? = stored(context) ?: builtIn()

    fun save(context: Context, settings: HeadsetSettings) {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val sealed = cipher.iv + cipher.doFinal(SavedHeadset.json(settings).toByteArray())
        prefs(context).edit().putString(SEALED, Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
    }

    private fun builtIn(): HeadsetSettings? =
        HeadsetSettings(BuildConfig.HEADSET_URL, BuildConfig.HEADSET_TOKEN).takeIf { isConfigured(it.url, it.token) }

    private fun stored(context: Context): HeadsetSettings? {
        val saved = prefs(context).getString(SEALED, null) ?: return null
        return when (val reading = SavedHeadset.read(saved, ::open)) {
            is SavedReading.Found -> reading.settings
            SavedReading.TryLater -> {
                Log.w(STORAGE_TAG, "the Keystore couldn't open the saved headset connection this time; keeping it")
                null
            }
            SavedReading.Damaged -> {
                Log.w(STORAGE_TAG, "the saved headset connection can't be read; clearing it and asking again")
                prefs(context).edit().remove(SEALED).apply()
                null
            }
        }
    }

    private fun open(iv: ByteArray, body: ByteArray): ByteArray =
        Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, iv)) }.doFinal(body)

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
