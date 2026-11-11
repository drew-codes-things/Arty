package app.arty.quest

import kotlin.math.sign
import org.junit.Assert.assertEquals
import org.junit.Test

class PanelLayoutTest {

    @Test
    fun sidePanelsTurnInTowardsTheUserNotOutwards() {
        val sidePanels = PANEL_SPOTS.filter { it.x != 0f }
        assertEquals(2, sidePanels.size)
        sidePanels.forEach { spot ->
            assertEquals("panel ${spot.id} at x=${spot.x} turns ${spot.turn} degrees", sign(spot.x), sign(spot.turn), 0f)
        }
    }

    @Test
    fun theOrbFacesStraightAtTheUser() {
        assertEquals(0f, PANEL_SPOTS.single { it.x == 0f }.turn, 0f)
    }
}
