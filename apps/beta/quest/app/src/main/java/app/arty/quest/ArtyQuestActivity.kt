package app.arty.quest

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
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

private const val MIC_REQUEST = 1
private const val EYE_HEIGHT = 1.3f
private const val PANEL_DISTANCE = 1.6f
private const val MIN_PANEL_HEIGHT = 0.5f
private const val MAX_PANEL_HEIGHT = 2.4f

class ArtyQuestActivity : AppSystemActivity() {

    private lateinit var listener: ArtyListener
    private var askedForMic = false

    override fun registerFeatures(): List<SpatialFeature> = listOf(VRFeature(this), ComposeFeature())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        listener = ArtyListener.get(applicationContext)
        PANEL_SPOTS.forEach { spot ->
            Entity.createPanelEntity(
                spot.id,
                Transform(Pose(Vector3(spot.x, EYE_HEIGHT, PANEL_DISTANCE), Quaternion(0f, spot.turn, 0f))),
                Grabbable(type = GrabbableType.PIVOT_Y, minHeight = MIN_PANEL_HEIGHT, maxHeight = MAX_PANEL_HEIGHT),
            )
        }
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
        listener.showSetupIfNeeded()
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            if (listener.canListen()) ListeningService.start(this)
        } else if (!askedForMic) {
            askedForMic = true
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MIC_REQUEST)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != MIC_REQUEST) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            listener.state.problem = ""
            if (listener.canListen()) ListeningService.start(this)
        } else {
            listener.state.problem = MIC_REFUSED
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
        val state = listener.state
        when (id) {
            R.id.orb_panel -> OrbPanel(state, listener::changeWake, listener::saveSetup, listener::saveSetupWithCode, listener::openSetup, listener::closeSetup)
            R.id.servers_panel -> ServersPanel(state)
            R.id.today_panel -> TodayPanel(state)
        }
    }
}
