package app.arty

import android.content.Context
import android.content.Intent
import java.security.MessageDigest
import java.security.SecureRandom

object LaunchToken {
    private const val PREFS = "launch-token"
    private const val TOKEN = "token"
    private const val EXTRA = "app.arty.launchToken"
    private const val TOKEN_BYTES = 32

    fun stamp(context: Context, intent: Intent): Intent = intent.putExtra(EXTRA, token(context))

    fun isOurs(context: Context, intent: Intent?): Boolean {
        val given = intent?.getStringExtra(EXTRA) ?: return false
        return MessageDigest.isEqual(given.toByteArray(), token(context).toByteArray())
    }

    @Synchronized
    private fun token(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(TOKEN, null)?.let { return it }
        val fresh = ByteArray(TOKEN_BYTES).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
        prefs.edit().putString(TOKEN, fresh).commit()
        return fresh
    }
}
