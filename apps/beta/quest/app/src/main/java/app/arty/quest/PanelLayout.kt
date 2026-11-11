package app.arty.quest

private const val SIDE_OFFSET = 1.05f
private const val SIDE_TURN_DEGREES = 25f

internal data class PanelSpot(val id: Int, val x: Float, val turn: Float, val width: Float, val height: Float)

internal val PANEL_SPOTS = listOf(
    PanelSpot(R.id.orb_panel, 0f, 0f, 0.7f, 0.7f),
    PanelSpot(R.id.servers_panel, -SIDE_OFFSET, -SIDE_TURN_DEGREES, 0.8f, 0.9f),
    PanelSpot(R.id.today_panel, SIDE_OFFSET, SIDE_TURN_DEGREES, 0.8f, 0.9f),
)
