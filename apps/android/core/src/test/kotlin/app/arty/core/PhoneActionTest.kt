package app.arty.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PhoneActionTest {
    private fun line(json: String) = "Done.\n\n<phone-action>$json</phone-action>"

    @Test
    fun theActionLineIsTakenOutOfTheReplyAndRead() {
        val (text, action) = PhoneAction.split(line("""{"kind":"alarm","hour":7,"minute":5,"label":"gym"}"""))
        assertEquals("Done.", text)
        assertEquals(PhoneAction(PhoneAction.ALARM, hour = 7, minute = 5, label = "gym"), action)
    }

    @Test
    fun oldSavedCallsAndTextsStillRead() {
        assertEquals(PhoneAction("text", who = "Sam", body = "Running late"),
            PhoneAction.fromJson("""{"kind":"text","who":"Sam","body":"Running late"}"""))
        assertEquals(PhoneAction("call", who = "Mum"), PhoneAction.fromJson("""{"kind":"call","who":"Mum","body":""}"""))
    }

    @Test
    fun eachKindRoundTripsThroughWhatIsSaved() {
        for (action in listOf(
            PhoneAction("text", who = "Sam", body = "hi"), PhoneAction(PhoneAction.ALARM, hour = 23, minute = 59),
            PhoneAction(PhoneAction.TIMER, seconds = 90, label = "tea"), PhoneAction(PhoneAction.APP, app = "Spotify"),
        )) {
            assertEquals(action, PhoneAction.fromJson(action.toJson()))
        }
    }

    @Test
    fun impossibleOrUnknownActionsAreDropped() {
        for (json in listOf(
            """{"kind":"alarm","hour":24,"minute":0}""", """{"kind":"alarm","hour":7,"minute":60}""", """{"kind":"alarm","hour":"7","minute":0}""",
            """{"kind":"timer","seconds":0}""", """{"kind":"timer","seconds":86401}""", """{"kind":"app","app":""}""",
            """{"kind":"app","app":"${"x".repeat(41)}"}""", """{"kind":"text","who":"","body":"x"}""", """{"kind":"launch","app":"x"}""",
            """{"kind":"app","app":"Bad\nName"}""", "not json", "",
        )) {
            assertNull(PhoneAction.fromJson(json), json)
        }
    }

    @Test
    fun theAllowCardShowsExactlyWhatWillHappen() {
        val text = PhoneAction("text", who = "Sam", body = "Running late")
        assertEquals(AllowCard("Send this text?", listOf("To: pick Sam from your contacts", "Text: Running late"), "Send"), text.card())
        assertEquals("To: Sam Smith (07700 900123)", text.card("Sam Smith (07700 900123)").lines[0])
        assertEquals(AllowCard("Set this alarm?", listOf("Time: 07:05", "Label: gym"), "Set alarm"),
            PhoneAction(PhoneAction.ALARM, hour = 7, minute = 5, label = "gym").card())
        assertEquals(listOf("Length: 1 hour 30 minutes"), PhoneAction(PhoneAction.TIMER, seconds = 5400).card().lines)
        assertEquals(listOf("App: Spotify"), PhoneAction(PhoneAction.APP, app = "Spotify").card("Spotify").lines)
    }

    @Test
    fun onlyCallsSkipTheCardBecauseTheDiallerAsksItself() {
        assertFalse(PhoneAction("call", who = "Mum").needsAllowCard)
        assertTrue(PhoneAction("text", who = "Mum").needsAllowCard)
        assertTrue(PhoneAction(PhoneAction.APP, app = "Maps").needsAllowCard)
    }

    @Test
    fun aCameraRequestOnlyOffersTheButtonAndKeepsTheQuestion() {
        val (text, action) = PhoneAction.split(line("""{"kind":"camera","question":"Is this plant healthy?"}"""))
        assertEquals("Done.", text)
        assertEquals(PhoneAction(PhoneAction.CAMERA, question = "Is this plant healthy?"), action)
        assertEquals("Take a photo", action!!.buttonLabel)
        assertFalse(action.needsAllowCard)
        assertFalse(action.startsWhenTheReplyArrives)
        assertTrue(PhoneAction("call", who = "Mum").startsWhenTheReplyArrives)
        assertEquals(action, PhoneAction.fromJson(action.toJson()))
        assertEquals(300, PhoneAction.fromJson("""{"kind":"camera","question":"${"q".repeat(400)}"}""")!!.question.length)
        assertEquals("", PhoneAction.fromJson("""{"kind":"camera"}""")!!.question)
    }

    @Test
    fun aPhotoFillsTheQuestionButNeverOverwritesWhatWasTyped() {
        val asked = PhoneAction(PhoneAction.CAMERA, question = "What breed is this?")
        assertEquals("What breed is this?", PhoneAction.photoQuestion(asked, ""))
        assertEquals("How old is it?", PhoneAction.photoQuestion(asked, "How old is it?"))
        assertEquals(PhoneAction.DEFAULT_PHOTO_QUESTION, PhoneAction.photoQuestion(PhoneAction(PhoneAction.CAMERA), " "))
        assertEquals("What is in this photo?", PhoneAction.photoQuestion(null, ""))
    }

    @Test
    fun timerLengthsReadNaturally() {
        assertEquals("1 minute 5 seconds", PhoneAction.timerWords(65))
        assertEquals("2 hours", PhoneAction.timerWords(7200))
    }

    @Test
    fun appsAreMatchedBestFirstAndOnlyAmongTheInstalledOnes() {
        val installed = listOf("Spotify", "Spotify Kids", "Maps", "Google Maps", "Camera", "Messages")
        assertEquals(listOf("Spotify", "Spotify Kids"), AppMatch.rank("spotify", installed))
        assertEquals(listOf("Maps", "Google Maps"), AppMatch.rank("Maps", installed))
        assertEquals(emptyList(), AppMatch.rank("Netflix", installed))
        assertEquals(emptyList(), AppMatch.rank(" ", installed))
    }
}
