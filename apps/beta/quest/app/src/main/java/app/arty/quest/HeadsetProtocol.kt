package app.arty.quest

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

enum class OrbState(val wireName: String) {
    IDLE("idle"),
    OFF("off"),
    LISTENING("listening"),
    THINKING("thinking"),
    SPEAKING("speaking");

    companion object {
        fun fromWire(name: String): OrbState = entries.firstOrNull { it.wireName == name } ?: IDLE
    }
}

data class ServerStatus(val name: String, val state: String, val ok: Boolean, val detail: String)

data class CalendarItem(val title: String, val start: String, val allDay: Boolean)

data class ReminderItem(val title: String, val nextRun: String)

data class Panels(
    val servers: List<ServerStatus> = emptyList(),
    val weather: String = "",
    val events: List<CalendarItem> = emptyList(),
    val reminders: List<ReminderItem> = emptyList(),
)

sealed interface ServerMessage {
    data class Hello(val wakeEnabled: Boolean) : ServerMessage
    data class State(val state: OrbState, val text: String, val seconds: Double) : ServerMessage
    data class Wake(val enabled: Boolean) : ServerMessage
    data class Action(val id: Int, val action: HeadsetAction) : ServerMessage
    data class PanelUpdate(val panels: Panels) : ServerMessage
}

sealed interface HeadsetAction {
    data class SpotifyPlay(val query: String) : HeadsetAction
    data class Media(val command: MediaCommand) : HeadsetAction
    data class Volume(val louder: Boolean) : HeadsetAction
    data class OpenApp(val name: String) : HeadsetAction
    data class Unknown(val name: String) : HeadsetAction
}

enum class MediaCommand { PLAY, PAUSE, NEXT, PREVIOUS }

object HeadsetProtocol {

    fun parse(text: String): ServerMessage? {
        val message = try {
            JSONObject(text)
        } catch (error: JSONException) {
            return null
        }
        return when (message.optString("type")) {
            "hello" -> ServerMessage.Hello(message.optBoolean("wake_enabled", true))
            "state" -> ServerMessage.State(
                OrbState.fromWire(message.optString("state")),
                message.optString("text"),
                message.optDouble("seconds", 0.0),
            )
            "wake" -> if (message.has("enabled")) ServerMessage.Wake(message.optBoolean("enabled")) else null
            "action" -> if (message.has("id")) ServerMessage.Action(message.optInt("id"), actionFrom(message)) else null
            "panels" -> ServerMessage.PanelUpdate(panelsFrom(message))
            else -> null
        }
    }

    fun actionResult(id: Int, say: String): String =
        JSONObject().put("type", "action_result").put("id", id).put("say", say).toString()

    fun wakeSetting(enabled: Boolean): String = JSONObject().put("type", "wake").put("enabled", enabled).toString()

    private fun actionFrom(message: JSONObject): HeadsetAction = when (val name = message.optString("action")) {
        "spotify_play" -> message.optString("query").trim().let {
            if (it.isEmpty()) HeadsetAction.Unknown(name) else HeadsetAction.SpotifyPlay(it)
        }
        "media_play" -> HeadsetAction.Media(MediaCommand.PLAY)
        "media_pause" -> HeadsetAction.Media(MediaCommand.PAUSE)
        "media_next" -> HeadsetAction.Media(MediaCommand.NEXT)
        "media_previous" -> HeadsetAction.Media(MediaCommand.PREVIOUS)
        "volume_up" -> HeadsetAction.Volume(louder = true)
        "volume_down" -> HeadsetAction.Volume(louder = false)
        "open_app" -> message.optString("app").trim().let {
            if (it.isEmpty()) HeadsetAction.Unknown(name) else HeadsetAction.OpenApp(it)
        }
        else -> HeadsetAction.Unknown(name)
    }

    private fun panelsFrom(message: JSONObject) = Panels(
        servers = message.optJSONArray("servers").objects().map {
            ServerStatus(it.optString("name"), it.optString("state"), it.optBoolean("ok"), it.optString("detail"))
        },
        weather = message.optString("weather"),
        events = message.optJSONArray("events").objects().map {
            CalendarItem(it.optString("title"), it.optString("start"), it.optBoolean("all_day"))
        },
        reminders = message.optJSONArray("reminders").objects().map {
            ReminderItem(it.optString("title"), it.optString("next_run"))
        },
    )

    private fun JSONArray?.objects(): List<JSONObject> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
}
