package app.arty.quest

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.Executors

private const val UPDATE_TAG = "ArtyUpdate"
private const val UPDATE_PREFS = "quest-updates"
private const val PREF_ENABLED = "updateCheck"
private const val PREF_DISMISSED = "dismissedUpdate"

class UpdateWatch(private val context: Context, private val state: QuestState) {
    private val main = Handler(Looper.getMainLooper())
    private val runner = Executors.newSingleThreadExecutor()
    private val prefs get() = context.getSharedPreferences(UPDATE_PREFS, Context.MODE_PRIVATE)
    private var latest: String? = null
    private var started = false
    private val check = Runnable { checkNow() }
    private val manifestAddress = siteManifestAddress(BuildConfig.ARTY_SITE_URL)
    private val releasePage = siteReleasePage(BuildConfig.ARTY_SITE_URL)
    val canCheck: Boolean = updateSwitchOffered(BuildConfig.ARTY_SITE_URL)

    fun start() {
        if (started) return
        started = true
        state.updateCheck = prefs.getBoolean(PREF_ENABLED, true)
        if (manifestAddress == null) {
            Log.i(UPDATE_TAG, "update check skipped (this build has no Arty site to ask)")
            return
        }
        main.postDelayed(check, FIRST_UPDATE_CHECK_MS)
    }

    private fun checkNow() {
        main.postDelayed(check, UPDATE_CHECK_EVERY_MS)
        val address = manifestAddress ?: return
        if (!state.updateCheck) return
        runner.execute {
            val found = try {
                fetchLatestVersion(BuildConfig.VERSION_NAME, address)
            } catch (error: Exception) {
                Log.i(UPDATE_TAG, updateSkippedLine(error))
                return@execute
            } catch (error: StackOverflowError) {
                Log.i(UPDATE_TAG, updateSkippedLine(error))
                return@execute
            }
            main.post {
                latest = found
                show()
            }
        }
    }

    private fun show() {
        state.update = updateNoticeFor(latest, BuildConfig.VERSION_NAME, prefs.getString(PREF_DISMISSED, null), state.updateCheck)
    }

    fun setEnabled(enabled: Boolean) {
        state.updateCheck = enabled
        prefs.edit().putBoolean(PREF_ENABLED, enabled).apply()
        show()
    }

    fun dismiss() {
        val shown = state.update ?: return
        prefs.edit().putString(PREF_DISMISSED, shown.version).apply()
        show()
    }

    fun openReleasePage() {
        val page = releasePage ?: return
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(page)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            context.startActivity(intent)
        } catch (error: ActivityNotFoundException) {
            Log.i(UPDATE_TAG, "no browser opened the download page (${error.javaClass.simpleName})")
        }
    }
}
