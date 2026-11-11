package app.arty.quest

import android.Manifest
import android.app.ActivityManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.Executors

private const val LISTENER_TAG = "ArtyListener"
private const val MIC_RETRY_MS = 3_000L
private const val DEFAULT_HEADSET_NAME = "Quest headset"
const val SAVE_FAILED = "This headset couldn't save the connection. Make a new setup code and try again."
const val MIC_LOST = "Arty can't hear you right now. Trying again..."

class ArtyListener private constructor(private val context: Context) : HeadsetEvents {

    val state = QuestState()
    private val main = Handler(Looper.getMainLooper())
    private val actionRunner = Executors.newSingleThreadExecutor()
    private val pairingRunner = Executors.newSingleThreadExecutor()
    private val actions = HeadsetActions(context, ::isInForeground)
    private val player = ReplyPlayer(context.cacheDir)
    private val mic = MicStreamer({ frame -> link?.sendAudio(frame) }, ::micStopped)
    private val retryMic = Runnable { if (running && state.connected) startMic() }
    @Volatile
    private var link: HeadsetLink? = null
    private var running = false
    private var keyRefused = false
    private var pairingAttempt = 0
    var onUnavailable: (() -> Unit)? = null

    fun showSetupIfNeeded() {
        if (QuestConnection.current(context) == null) openSetup()
    }

    fun canListen(): Boolean = !keyRefused && QuestConnection.current(context) != null

    fun start(): Boolean {
        if (running) {
            if (state.connected) startMic()
            return true
        }
        if (keyRefused) return false
        val settings = QuestConnection.current(context)
        if (settings == null) {
            openSetup()
            return false
        }
        running = true
        link = HeadsetLink(settings.url, settings.token, settings.device, this)
        listen()
        return true
    }

    fun stop() {
        running = false
        main.removeCallbacks(retryMic)
        mic.stop()
        link?.close()
        link = null
        player.stop()
        state.connected = false
    }

    private fun listen() {
        link?.open()
    }

    private fun isInForeground(): Boolean {
        val info = ActivityManager.RunningAppProcessInfo()
        ActivityManager.getMyMemoryState(info)
        return info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND
    }

    private fun startMic() {
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            state.problem = MIC_REFUSED
            return
        }
        if (state.problem == MIC_REFUSED) state.problem = ""
        if (mic.start() && state.problem == MIC_LOST) state.problem = ""
    }

    private fun micStopped(reason: String) {
        Log.w(LISTENER_TAG, "the microphone stopped: $reason")
        state.problem = MIC_LOST
        main.postDelayed(retryMic, MIC_RETRY_MS)
    }

    fun changeWake(enabled: Boolean) {
        state.wakeEnabled = enabled
        link?.sendText(HeadsetProtocol.wakeSetting(enabled))
    }

    fun openSetup() {
        state.canCancelSetup = link != null
        state.setupOpen = true
    }

    fun closeSetup() {
        pairingAttempt += 1
        state.setupBusy = false
        state.setupOpen = false
        state.setupProblem = ""
    }

    fun saveSetupWithCode(address: String, code: String) {
        val url = headsetUrlFrom(address)
        val problem = headsetAddressProblem(url) ?: setupCodeProblem(code)
        if (problem != null) {
            state.setupProblem = problem
            return
        }
        state.setupBusy = true
        state.setupProblem = ""
        pairingAttempt += 1
        val attempt = pairingAttempt
        pairingRunner.execute {
            val outcome = try {
                PairingClient.redeem(pairUrlFrom(url), normalisedSetupCode(code), headsetName())
            } catch (error: RuntimeException) {
                PairingOutcome.Refused(PAIRING_UNREACHABLE)
            }
            main.post {
                if (attempt != pairingAttempt) return@post
                state.setupBusy = false
                when (outcome) {
                    is PairingOutcome.Key -> store(url, outcome.token, outcome.device)
                    is PairingOutcome.Refused -> state.setupProblem = outcome.reason
                }
            }
        }
    }

    fun saveSetup(address: String, key: String) {
        val url = headsetUrlFrom(address)
        val problem = manualKeyProblem(BuildConfig.MANUAL_KEY_ALLOWED) ?: headsetAddressProblem(url) ?: headsetTokenProblem(key)
        if (problem != null) {
            state.setupProblem = problem
            return
        }
        store(url, key.trim(), "")
    }

    private fun headsetName(): String = Build.MODEL?.takeIf { it.isNotBlank() } ?: DEFAULT_HEADSET_NAME

    private fun store(url: String, key: String, device: String) {
        try {
            QuestConnection.save(context, HeadsetSettings(url, key, device))
        } catch (error: Exception) {
            Log.e(LISTENER_TAG, "the connection couldn't be saved", error)
            state.setupProblem = SAVE_FAILED
            return
        }
        closeSetup()
        keyRefused = false
        stop()
        state.problem = ""
        begin()
    }

    private fun begin() {
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            ListeningService.start(context)
        } else {
            start()
        }
    }

    override fun connected() {
        state.connected = true
        if (state.problem != MIC_REFUSED && state.problem != MIC_LOST) state.problem = ""
        if (running) startMic()
    }

    override fun disconnected(reason: String) {
        state.connected = false
        Log.w(LISTENER_TAG, "disconnected: $reason")
        main.removeCallbacks(retryMic)
        mic.stop()
        if (reason == IDENTITY_REFUSED) state.problem = IDENTITY_REFUSED
        if (reason == LINK_GAVE_UP) {
            state.problem = LINK_GAVE_UP
            stop()
            onUnavailable?.invoke()
            return
        }
        if (!needsNewKey(reason)) return
        keyRefused = true
        stop()
        onUnavailable?.invoke()
        state.setupProblem = KEY_REFUSED_HELP
        state.canCancelSetup = false
        state.setupOpen = true
    }

    override fun message(message: ServerMessage) {
        state.apply(message)
        if (message is ServerMessage.Action) runAction(message)
    }

    override fun audio(wav: ByteArray) = player.play(wav)

    private fun runAction(request: ServerMessage.Action) {
        actionRunner.execute {
            val say = try {
                actions.run(request.action)
            } catch (error: SecurityException) {
                Log.e(LISTENER_TAG, "headset action ${request.action} was blocked", error)
                ACTION_BLOCKED
            } catch (error: RuntimeException) {
                Log.e(LISTENER_TAG, "headset action ${request.action} failed", error)
                ACTION_FAILED
            }
            main.post { link?.sendText(HeadsetProtocol.actionResult(request.id, say)) }
        }
    }

    companion object {
        @Volatile
        private var instance: ArtyListener? = null

        fun get(context: Context): ArtyListener =
            instance ?: synchronized(this) { instance ?: ArtyListener(context.applicationContext).also { instance = it } }
    }
}
