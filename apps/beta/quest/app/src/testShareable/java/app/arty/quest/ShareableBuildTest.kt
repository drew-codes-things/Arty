package app.arty.quest

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ShareableBuildTest {
    @Test
    fun theShareableBuildCarriesNoHeadsetAddressOrKey() {
        assertTrue(BuildConfig.HEADSET_TOKEN.isEmpty())
        assertTrue(BuildConfig.HEADSET_URL.isEmpty())
    }

    @Test
    fun theShareableBuildOnlyPairsWithASetupCode() {
        assertFalse(BuildConfig.MANUAL_KEY_ALLOWED)
    }
}
