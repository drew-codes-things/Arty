package app.arty.quest

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HeadsetProtocolTest {

    @Test
    fun helloCarriesTheWakeSetting() {
        assertEquals(ServerMessage.Hello(false), HeadsetProtocol.parse("""{"type":"hello","wake_enabled":false}"""))
    }

    @Test
    fun stateMessagesMapToOrbStates() {
        assertEquals(
            ServerMessage.State(OrbState.THINKING, "what's the weather", 0.0),
            HeadsetProtocol.parse("""{"type":"state","state":"thinking","text":"what's the weather","seconds":0}"""),
        )
        assertEquals(OrbState.IDLE, (HeadsetProtocol.parse("""{"type":"state","state":"dancing"}""") as ServerMessage.State).state)
    }

    @Test
    fun spotifyAndMediaActionsAreRecognised() {
        assertEquals(
            ServerMessage.Action(3, HeadsetAction.SpotifyPlay("Timber by Pitbull")),
            HeadsetProtocol.parse("""{"type":"action","id":3,"action":"spotify_play","query":" Timber by Pitbull "}"""),
        )
        assertEquals(
            ServerMessage.Action(4, HeadsetAction.Media(MediaCommand.PAUSE)),
            HeadsetProtocol.parse("""{"type":"action","id":4,"action":"media_pause"}"""),
        )
        assertEquals(
            ServerMessage.Action(5, HeadsetAction.Volume(louder = false)),
            HeadsetProtocol.parse("""{"type":"action","id":5,"action":"volume_down"}"""),
        )
    }

    @Test
    fun openAppCarriesTheSpokenName() {
        assertEquals(
            ServerMessage.Action(8, HeadsetAction.OpenApp("Beat Saber")),
            HeadsetProtocol.parse("""{"type":"action","id":8,"action":"open_app","app":" Beat Saber "}"""),
        )
        assertEquals(
            ServerMessage.Action(9, HeadsetAction.Unknown("open_app")),
            HeadsetProtocol.parse("""{"type":"action","id":9,"action":"open_app","app":""}"""),
        )
    }

    @Test
    fun unknownOrEmptyActionsAreStillAnsweredNotDropped() {
        assertEquals(
            ServerMessage.Action(6, HeadsetAction.Unknown("teleport")),
            HeadsetProtocol.parse("""{"type":"action","id":6,"action":"teleport"}"""),
        )
        assertEquals(
            ServerMessage.Action(7, HeadsetAction.Unknown("spotify_play")),
            HeadsetProtocol.parse("""{"type":"action","id":7,"action":"spotify_play","query":"  "}"""),
        )
    }

    @Test
    fun panelsAreReadInFull() {
        val panels = HeadsetProtocol.parse(
            """{"type":"panels","weather":"Cardiff: 12C, light rain",
                "servers":[{"name":"core-sys","state":"running","ok":true,"detail":"CPU 4%"},{"name":"ai","state":"stopped","ok":false}],
                "events":[{"title":"Dentist","start":"2026-11-05T15:00","all_day":false}],
                "reminders":[{"title":"Bins","next_run":"2026-11-05T19:00"}]}""",
        ) as ServerMessage.PanelUpdate
        assertEquals("Cardiff: 12C, light rain", panels.panels.weather)
        assertEquals(listOf(ServerStatus("core-sys", "running", true, "CPU 4%"), ServerStatus("ai", "stopped", false, "")), panels.panels.servers)
        assertEquals(listOf(CalendarItem("Dentist", "2026-11-05T15:00", false)), panels.panels.events)
        assertEquals(listOf(ReminderItem("Bins", "2026-11-05T19:00")), panels.panels.reminders)
    }

    @Test
    fun missingPanelListsAreEmpty() {
        assertEquals(Panels(), (HeadsetProtocol.parse("""{"type":"panels"}""") as ServerMessage.PanelUpdate).panels)
    }

    @Test
    fun junkIsIgnored() {
        assertNull(HeadsetProtocol.parse("not json"))
        assertNull(HeadsetProtocol.parse("""{"type":"mystery"}"""))
        assertNull(HeadsetProtocol.parse("""{"type":"action","action":"media_play"}"""))
        assertNull(HeadsetProtocol.parse("""{"type":"wake"}"""))
    }

    @Test
    fun repliesMatchWhatTheServerReads() {
        val result = JSONObject(HeadsetProtocol.actionResult(9, "Please log in"))
        assertEquals("action_result", result.getString("type"))
        assertEquals(9, result.getInt("id"))
        assertEquals("Please log in", result.getString("say"))
        val wake = JSONObject(HeadsetProtocol.wakeSetting(false))
        assertEquals("wake", wake.getString("type"))
        assertFalse(wake.getBoolean("enabled"))
    }

    @Test
    fun reconnectingBacksOffToThirtySeconds() {
        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L), (0..6).map { reconnectDelayMs(it) })
    }

    @Test
    fun reconnectingGivesUpAfterSeveralMinutesOfFailures() {
        (0 until MAX_FAILED_ATTEMPTS).forEach { assertFalse("$it", shouldStopRetrying(it)) }
        assertTrue(shouldStopRetrying(MAX_FAILED_ATTEMPTS))
        val waitedMs = (0 until MAX_FAILED_ATTEMPTS).sumOf { reconnectDelayMs(it) }
        assertTrue("$waitedMs", waitedMs in 300_000L..900_000L)
    }

    @Test
    fun theHeadsetNeedsAWebSocketAddressAndAKey() {
        assertTrue(isConfigured("ws://192.168.1.16:8790/ws/headset", "key"))
        assertFalse(isConfigured("ws://192.168.1.16:8790/ws/headset", " "))
        assertFalse(isConfigured("http://192.168.1.16:8790/ws/headset", "key"))
        assertFalse(isConfigured("", "key"))
    }

    @Test
    fun orbCaptionsFollowTheWakeSetting() {
        assertEquals("Say \"Arty ...\"", orbCaption(OrbState.IDLE, wakeEnabled = true))
        assertEquals("Wake word off", orbCaption(OrbState.IDLE, wakeEnabled = false))
        assertEquals("Listening...", orbCaption(OrbState.LISTENING, wakeEnabled = true))
    }

    @Test
    fun aThirtyMillisecondMicFrameMatchesTheServer() {
        assertEquals(960, MIC_FRAME_BYTES)
    }

    @Test
    fun youtubeAndAppPlayActionsAreRecognised() {
        assertEquals(
            ServerMessage.Action(8, HeadsetAction.YoutubePlay("Timber", "https://www.youtube.com/watch?v=hHUbLv4ThOo")),
            HeadsetProtocol.parse("""{"type":"action","id":8,"action":"youtube_play","query":" Timber ","url":"https://www.youtube.com/watch?v=hHUbLv4ThOo"}"""),
        )
        assertEquals(
            ServerMessage.Action(9, HeadsetAction.YoutubePlay("Timber", "")),
            HeadsetProtocol.parse("""{"type":"action","id":9,"action":"youtube_play","query":"Timber"}"""),
        )
        assertEquals(
            ServerMessage.Action(10, HeadsetAction.AppPlay("Deezer", "some jazz")),
            HeadsetProtocol.parse("""{"type":"action","id":10,"action":"app_play","app":" Deezer ","query":"some jazz"}"""),
        )
    }

    @Test
    fun playActionsWithoutWhatToPlayAreUnknown() {
        assertEquals(
            ServerMessage.Action(11, HeadsetAction.Unknown("youtube_play")),
            HeadsetProtocol.parse("""{"type":"action","id":11,"action":"youtube_play","query":"  "}"""),
        )
        assertEquals(
            ServerMessage.Action(12, HeadsetAction.Unknown("app_play")),
            HeadsetProtocol.parse("""{"type":"action","id":12,"action":"app_play","app":"","query":"x"}"""),
        )
    }
}
