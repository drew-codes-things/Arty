package app.arty.quest

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val PanelTop = Color(0xF21A0A0D)
private val PanelBottom = Color(0xF2300C12)
private val ArtyRed = Color(0xFFE03A45)
private val SoftText = Color(0xFFE8DADB)
private val DimText = Color(0xFFA88E91)
private val Healthy = Color(0xFF52C77A)

fun pulseMillis(state: OrbState): Int = when (state) {
    OrbState.LISTENING -> 600
    OrbState.THINKING -> 900
    OrbState.SPEAKING -> 450
    OrbState.IDLE -> 2400
    OrbState.OFF -> 4000
}

@Composable
private fun PanelFrame(content: @Composable () -> Unit) {
    Box(
        Modifier
            .fillMaxSize()
            .clip(RoundedCornerShape(28.dp))
            .background(Brush.verticalGradient(listOf(PanelTop, PanelBottom)))
            .padding(28.dp),
    ) { content() }
}

@Composable
private fun Heading(text: String) {
    Text(text, color = ArtyRed, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(12.dp))
}

@Composable
private fun Line(text: String, colour: Color = SoftText) {
    Text(text, color = colour, fontSize = 17.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
}

@Composable
fun Orb(state: OrbState, modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "orb")
    val pulse by transition.animateFloat(
        initialValue = 0.82f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(pulseMillis(state), easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "pulse",
    )
    val glow = if (state == OrbState.OFF) DimText else ArtyRed
    Canvas(modifier) {
        val radius = size.minDimension / 2 * pulse
        drawCircle(Brush.radialGradient(listOf(glow.copy(alpha = 0.55f), Color.Transparent), center, radius), radius)
        drawCircle(Brush.radialGradient(listOf(Color.White, glow), center, radius * 0.45f), radius * 0.45f)
    }
}

@Composable
private fun SetupField(label: String, value: String, onChange: (String) -> Unit, secret: Boolean) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = if (secret) KeyboardType.Password else KeyboardType.Uri, autoCorrectEnabled = false),
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = SoftText, unfocusedTextColor = SoftText, focusedBorderColor = ArtyRed,
            unfocusedBorderColor = DimText, focusedLabelColor = ArtyRed, unfocusedLabelColor = DimText, cursorColor = ArtyRed,
        ),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
fun SetupForm(state: QuestState, onSave: (String, String) -> Unit, onCancel: () -> Unit) {
    var address by rememberSaveable { mutableStateOf("") }
    var key by rememberSaveable { mutableStateOf("") }
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Heading("Connect to Arty")
        Line(SETUP_HELP, DimText)
        SetupField("Headset address, e.g. ws://192.168.1.16:8790/ws/headset", address, { address = it }, secret = false)
        SetupField("Headset key", key, { key = it }, secret = true)
        if (state.setupProblem.isNotBlank()) Text(state.setupProblem, color = ArtyRed, fontSize = 15.sp, maxLines = 3)
        Spacer(Modifier.weight(1f))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            if (state.canCancelSetup) TextButton(onClick = onCancel) { Text("Cancel", color = DimText) }
            Button(onClick = { onSave(address, key) }, colors = ButtonDefaults.buttonColors(containerColor = ArtyRed)) {
                Text("Connect")
            }
        }
    }
}

@Composable
fun OrbPanel(state: QuestState, onWakeChange: (Boolean) -> Unit, onSetup: (String, String) -> Unit, onOpenSetup: () -> Unit, onCancelSetup: () -> Unit) {
    PanelFrame {
        if (state.setupOpen) {
            SetupForm(state, onSetup, onCancelSetup)
            return@PanelFrame
        }
        Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
            Orb(state.orb, Modifier.size(170.dp))
            Spacer(Modifier.height(10.dp))
            val caption = when {
                state.problem.isNotBlank() -> state.problem
                !state.connected -> CONNECTING
                else -> orbCaption(state.orb, state.wakeEnabled)
            }
            Text(caption, color = SoftText, fontSize = 18.sp, maxLines = 3)
            if (state.heard.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Line("“${state.heard}”", DimText)
            }
            Spacer(Modifier.weight(1f))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Wake word", color = SoftText, fontSize = 16.sp)
                Spacer(Modifier.size(12.dp))
                Switch(
                    checked = state.wakeEnabled,
                    onCheckedChange = onWakeChange,
                    enabled = state.connected,
                    colors = SwitchDefaults.colors(checkedTrackColor = ArtyRed),
                )
                Spacer(Modifier.size(12.dp))
                TextButton(onClick = onOpenSetup) { Text("Server", color = DimText, fontSize = 16.sp) }
            }
        }
    }
}

@Composable
fun ServersPanel(state: QuestState) {
    PanelFrame {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Heading("Servers")
            if (state.panels.servers.isEmpty()) Line("Waiting for the first check...", DimText)
            state.panels.servers.forEach { server ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(12.dp).clip(CircleShape).background(if (server.ok) Healthy else ArtyRed))
                    Spacer(Modifier.size(10.dp))
                    Line(listOf(server.name, server.state, server.detail).filter { it.isNotBlank() }.joinToString(" · "))
                }
            }
        }
    }
}

@Composable
fun TodayPanel(state: QuestState) {
    PanelFrame {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Heading("Weather")
            Line(state.panels.weather.ifBlank { "Waiting for the forecast..." })
            Spacer(Modifier.height(10.dp))
            Heading("Calendar")
            if (state.panels.events.isEmpty()) Line("Nothing in the next week.", DimText)
            state.panels.events.forEach { Line(if (it.allDay) "${it.start} · ${it.title} (all day)" else "${it.start} · ${it.title}") }
            Spacer(Modifier.height(10.dp))
            Heading("Reminders")
            if (state.panels.reminders.isEmpty()) Line("No reminders set.", DimText)
            state.panels.reminders.forEach { Line("${it.nextRun} · ${it.title}") }
        }
    }
}
