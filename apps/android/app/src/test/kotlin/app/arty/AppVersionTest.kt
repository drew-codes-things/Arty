package app.arty

import app.arty.core.AppUpdate
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class AppVersionTest {
    @Test
    fun `this release is Arty 1_8_0 with build number 16`() {
        assertEquals("1.8.0", BuildConfig.VERSION_NAME)
        assertEquals(16, BuildConfig.VERSION_CODE)
    }

    @Test
    fun `the app compares its own version with the tag of the latest release`() {
        assertTrue(AppUpdate.isNewer(AppUpdate.releaseVersion("""{"tag_name": "v1.8.1"}"""), BuildConfig.VERSION_NAME))
        assertFalse(AppUpdate.isNewer(AppUpdate.releaseVersion("""{"tag_name": "v1.8.0"}"""), BuildConfig.VERSION_NAME))
        assertEquals("Version 1.8.1 is available", AppUpdate.noticeFor("1.8.1", BuildConfig.VERSION_NAME, null, enabled = true)?.text)
    }

    @Test
    fun `the shareable build asks GitHub and the personal build asks the site it was built for`() {
        val source = AppUpdate.sourceFor(BuildConfig.ARTY_BASE_URL)
        val builtForASite = BuildConfig.FLAVOR == "personal" && BuildConfig.ARTY_BASE_URL.isNotBlank()
        if (builtForASite) {
            assertTrue(source.address.endsWith(AppUpdate.SITE_MANIFEST_PATH))
        } else {
            assertEquals(AppUpdate.RELEASE_API_URL, source.address)
        }
    }

    @Test
    fun `the update check logs only the kind of failure`() {
        val model = java.io.File("src/main/kotlin/app/arty/ArtyViewModel.kt").readText()
        val check = model.substringAfter("private suspend fun checkForUpdate()").substringBefore("private fun showAppUpdate()")
        assertEquals(2, Regex("AppUpdate\\.skippedLine\\(error\\)").findAll(check).count())
        assertFalse(check.contains("error.message"))
        assertTrue(check.contains("catch (error: StackOverflowError)"))
        assertFalse(java.io.File("src/main/kotlin/app/arty/ui/UpdateBanner.kt").readText().contains("error.message"))
    }
}
