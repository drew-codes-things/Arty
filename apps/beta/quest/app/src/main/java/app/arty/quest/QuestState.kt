package app.arty.quest

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

const val SETUP_HELP = "Enter your Arty voice server's address and the setup code it gives you. Or type the long headset key instead."
const val KEY_REFUSED_HELP = "Arty refused that headset key. Check it and connect again."
const val CONNECTING = "Connecting to Arty..."
const val MIC_REFUSED = "Arty can't hear you: allow the microphone in the headset's app permissions."

fun orbCaption(state: OrbState, wakeEnabled: Boolean): String = when (state) {
    OrbState.LISTENING -> "Listening..."
    OrbState.THINKING -> "Thinking..."
    OrbState.SPEAKING -> "Speaking"
    OrbState.OFF -> "Wake word off"
    OrbState.IDLE -> if (wakeEnabled) "Say \"Arty ...\"" else "Wake word off"
}

class QuestState {
    var connected by mutableStateOf(false)
    var problem by mutableStateOf("")
    var orb by mutableStateOf(OrbState.IDLE)
    var heard by mutableStateOf("")
    var wakeEnabled by mutableStateOf(true)
    var panels by mutableStateOf(Panels())
    var setupOpen by mutableStateOf(false)
    var setupProblem by mutableStateOf("")
    var setupBusy by mutableStateOf(false)
    var canCancelSetup by mutableStateOf(false)

    fun apply(message: ServerMessage) {
        when (message) {
            is ServerMessage.Hello -> wakeEnabled = message.wakeEnabled
            is ServerMessage.Wake -> wakeEnabled = message.enabled
            is ServerMessage.State -> {
                orb = message.state
                if (message.text.isNotBlank()) heard = message.text
            }
            is ServerMessage.PanelUpdate -> panels = message.panels
            is ServerMessage.Action -> Unit
        }
    }
}
