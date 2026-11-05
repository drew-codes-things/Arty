package app.arty.quest

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.ComposeView
import com.meta.spatial.compose.ComposeFeature
import com.meta.spatial.compose.ComposeViewPanelRegistration
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.SpatialFeature
import com.meta.spatial.core.Vector3
import com.meta.spatial.toolkit.AppSystemActivity
import com.meta.spatial.toolkit.DpPerMeterDisplayOptions
import com.meta.spatial.toolkit.Grabbable
import com.meta.spatial.toolkit.GrabbableType
import com.meta.spatial.toolkit.PanelRegistration
import com.meta.spatial.toolkit.PanelStyleOptions
import com.meta.spatial.toolkit.QuadShapeOptions
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.UIPanelSettings
import com.meta.spatial.toolkit.createPanelEntity
import com.meta.spatial.vr.VRFeature
import java.util.concurrent.Executors

private const val ACTIVITY_TAG = "ArtyQuest"
private const val MIC_REQUEST = 1
private const val EYE_HEIGHT = 1.3f
private const val PANEL_DISTANCE = 1.6f
private const val SIDE_OFFSET = 1.05f
private const val SIDE_TURN_DEGREES = 25f
private const val MIN_PANEL_HEIGHT = 0.5f
private const val MAX_PANEL_HEIGHT = 2.4f

private data class PanelSpot(val id: Int, val x: Float, val turn: Float, val width: Float, val height: Float)

private val PANEL_SPOTS = listOf(
    PanelSpot(R.id.orb_panel, 0f, 0f, 0.7f, 0.7f),
    PanelSpot(R.id.servers_panel, -SIDE_OFFSET, SIDE_TURN_DEGREES, 0.8f, 0.9f),
    PanelSpot(R.id.today_panel, SIDE_OFFSET, -SIDE_TURN_DEGREES, 0.8f, 0.9f),
)

class ArtyQuestActivity : AppSystemActivity(), HeadsetEvents {

    private val state = QuestState()
    private val actionRunner = Executors.newSingleThreadExecutor()
    private lateinit var actions: HeadsetActions
    private lateinit var player: ReplyPlayer
    private var link: HeadsetLink? = null
    private var mic: MicStreamer? = null

    override fun registerFeatures(): List<SpatialFeature> = listOf(VRFeature(this), ComposeFeature())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        actions = HeadsetActions(applicationContext)
        player = ReplyPlayer(cacheDir)
        PANEL_SPOTS.forEach { spot ->
            Entity.createPanelEntity(
                spot.id,
                Transform(Pose(Vector3(spot.x, EYE_HEIGHT, PANEL_DISTANCE), Quaternion(0f, spot.turn, 0f))),
                Grabbable(type = GrabbableType.PIVOT_Y, minHeight = MIN_PANEL_HEIGHT, maxHeight = MAX_PANEL_HEIGHT),
            )
        }
        mic = MicStreamer { frame -> link?.sendAudio(frame) }
        val settings = QuestConnection.current(applicationContext)
        if (settings == null) {
            openSetup(canCancel = false)
            return
        }
        link = HeadsetLink(settings.url, settings.token, this)
    }

    override fun onSceneReady() {
        super.onSceneReady()
        scene.enablePassthrough(true)
    }

    override fun onResume() {
        super.onResume()
        startListening()
    }

    private fun startListening() {
        val currentLink = link ?: return
        currentLink.open()
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            mic?.start()
        } else {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MIC_REQUEST)
        }
    }

    override fun onPause() {
        mic?.stop()
        link?.close()
        player.stop()
        state.connected = false
        super.onPause()
    }

    override fun onDestroy() {
        actionRunner.shutdownNow()
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != MIC_REQUEST) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            state.problem = ""
            mic?.start()
        } else {
            state.problem = MIC_REFUSED
        }
    }

    override fun registerPanels(): List<PanelRegistration> = PANEL_SPOTS.map { spot ->
        ComposeViewPanelRegistration(
            spot.id,
            composeViewCreator = { _, context -> ComposeView(context).apply { setContent { PanelContent(spot.id) } } },
            settingsCreator = {
                UIPanelSettings(
                    shape = QuadShapeOptions(width = spot.width, height = spot.height),
                    style = PanelStyleOptions(themeResourceId = R.style.PanelAppThemeTransparent),
                    display = DpPerMeterDisplayOptions(),
                )
            },
        )
    }

    @Composable
    private fun PanelContent(id: Int) {
        when (id) {
            R.id.orb_panel -> OrbPanel(state, ::changeWake, ::saveSetup, { openSetup(canCancel = link != null) }, ::closeSetup)
            R.id.servers_panel -> ServersPanel(state)
            R.id.today_panel -> TodayPanel(state)
        }
    }

    private fun openSetup(canCancel: Boolean) {
        state.canCancelSetup = canCancel
        state.setupOpen = true
    }

    private fun closeSetup() {
        state.setupOpen = false
        state.setupProblem = ""
    }

    private fun saveSetup(address: String, key: String) {
        val problem = headsetAddressProblem(address) ?: headsetTokenProblem(key)
        if (problem != null) {
            state.setupProblem = problem
            return
        }
        val settings = HeadsetSettings(address.trim(), key.trim())
        QuestConnection.save(applicationContext, settings)
        closeSetup()
        mic?.stop()
        link?.close()
        state.connected = false
        state.problem = ""
        link = HeadsetLink(settings.url, settings.token, this)
        startListening()
    }

    private fun changeWake(enabled: Boolean) {
        state.wakeEnabled = enabled
        link?.sendText(HeadsetProtocol.wakeSetting(enabled))
    }

    override fun connected() {
        state.connected = true
        if (state.problem != MIC_REFUSED) state.problem = ""
    }

    override fun disconnected(reason: String) {
        state.connected = false
        Log.w(ACTIVITY_TAG, "disconnected: $reason")
        if (!needsNewKey(reason)) return
        mic?.stop()
        link?.close()
        link = null
        state.setupProblem = KEY_REFUSED_HELP
        openSetup(canCancel = false)
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
                Log.e(ACTIVITY_TAG, "headset action ${request.action} was blocked", error)
                UNKNOWN_ACTION
            }
            runOnUiThread { link?.sendText(HeadsetProtocol.actionResult(request.id, say)) }
        }
    }
}
