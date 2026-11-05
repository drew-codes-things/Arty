package app.arty.ui

import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.arty.ArtyViewModel
import app.arty.R
import kotlinx.coroutines.delay

private enum class CallPhase(val label: String) {
    Listening("Listening…"),
    Thinking("Thinking"),
    Speaking("Speaking · tap to interrupt"),
    Muted("Muted"),
}

/**
 * A hands-free voice call: listen, send what was said, read the reply aloud, listen again.
 * Everything lands in the current chat, so the call can be read back afterwards.
 */
@Composable
fun CallScreen(vm: ArtyViewModel) {
    val context = LocalContext.current
    var muted by remember { mutableStateOf(false) }
    var heard by remember { mutableStateOf("") }
    var problem by remember { mutableStateOf<String?>(null) }
    // Bumped to start listening again after silence, without the phase changing.
    var listenRound by remember { mutableIntStateOf(0) }
    val phase = when {
        vm.speaking -> CallPhase.Speaking // the first sentences can be read out while Arty writes the rest
        vm.thinkingSince != null -> CallPhase.Thinking
        muted -> CallPhase.Muted
        else -> CallPhase.Listening
    }
    val currentPhase by rememberUpdatedState(phase)
    // Only listen again while still in the listening phase: cancelling the recogniser can itself report an error.
    fun listenAgain() {
        if (currentPhase == CallPhase.Listening) listenRound++
    }

    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    BackHandler { vm.endCall() }

    val recognizer = remember { SpeechRecognizer.createSpeechRecognizer(context) }
    DisposableEffect(recognizer) {
        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
                heard = text
                if (text.isBlank()) listenAgain() else vm.send(text, spoken = true)
            }

            override fun onPartialResults(partialResults: Bundle?) {
                partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let { heard = it }
            }

            override fun onError(error: Int) {
                when (error) {
                    // Nothing said, or it stopped hearing: just listen again.
                    SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT, SpeechRecognizer.ERROR_CLIENT,
                    SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> listenAgain()
                    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> {
                        problem = "Arty needs microphone access for calls."
                        muted = true
                    }
                    else -> {
                        problem = "Speech recognition stopped (error $error). Tap the microphone to try again."
                        muted = true
                    }
                }
            }

            override fun onReadyForSpeech(params: Bundle?) {
                problem = null
            }

            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        })
        onDispose { recognizer.destroy() }
    }

    LaunchedEffect(phase, listenRound) {
        if (phase != CallPhase.Listening) {
            recognizer.cancel()
            return@LaunchedEffect
        }
        delay(250) // let read-aloud release the audio first, so Arty doesn't hear itself
        heard = ""
        recognizer.startListening(
            Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-GB")
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true),
        )
    }

    val lastReply = vm.current.messages.lastOrNull { it.role == "assistant" }?.content.orEmpty()
    Column(
        Modifier.fillMaxSize().artyBackground().systemBarsPadding().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Call with Arty", style = MaterialTheme.typography.titleMedium, color = ArtyColors.TextDim)
        Spacer(Modifier.weight(1f))
        Orb(phase, onTap = { if (phase == CallPhase.Speaking) vm.stopSpeaking() })
        Spacer(Modifier.height(28.dp))
        PhaseLabel(phase, vm.thinkingSince)
        Spacer(Modifier.height(16.dp))
        val caption = when {
            problem != null -> problem!!
            phase == CallPhase.Listening || phase == CallPhase.Thinking -> heard
            phase == CallPhase.Speaking -> speakableText(lastReply)
            else -> ""
        }
        Text(
            caption,
            style = MaterialTheme.typography.bodyLarge,
            color = if (problem != null) ArtyColors.Signal else ArtyColors.TextDim,
            textAlign = TextAlign.Center,
            maxLines = 6,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().height(150.dp),
        )
        Spacer(Modifier.weight(1f))
        Row(horizontalArrangement = Arrangement.spacedBy(48.dp), verticalAlignment = Alignment.CenterVertically) {
            RoundButton(
                icon = ArtyIcons.Mic,
                label = if (muted) "Unmute" else "Mute",
                background = if (muted) ArtyColors.Text else ArtyColors.PanelRaised,
                tint = if (muted) ArtyColors.Void else ArtyColors.Text,
                onClick = {
                    problem = null
                    muted = !muted
                },
            )
            RoundButton(icon = ArtyIcons.HangUp, label = "End call", background = ArtyColors.Signal, tint = Color.White, onClick = vm::endCall)
        }
    }
}

@Composable
private fun Orb(phase: CallPhase, onTap: () -> Unit) {
    val pulse = rememberInfiniteTransition(label = "orb")
    val speed = when (phase) {
        CallPhase.Speaking -> 450
        CallPhase.Thinking -> 900
        else -> 1600
    }
    val grow by pulse.animateFloat(
        initialValue = 1f,
        targetValue = if (phase == CallPhase.Muted) 1f else 1.08f,
        animationSpec = infiniteRepeatable(tween(speed), RepeatMode.Reverse),
        label = "grow",
    )
    val ring = when (phase) {
        CallPhase.Listening -> ArtyColors.Muted
        CallPhase.Speaking -> ArtyColors.Signal
        CallPhase.Thinking -> ArtyColors.Caution
        CallPhase.Muted -> ArtyColors.Line
    }
    Box(
        Modifier.size(200.dp).scale(grow).clip(CircleShape).background(ArtyColors.Panel).border(3.dp, ring, CircleShape).clickable(onClick = onTap),
        contentAlignment = Alignment.Center,
    ) {
        Image(painterResource(R.drawable.arty_symbol), contentDescription = "Arty", modifier = Modifier.size(110.dp))
    }
}

@Composable
private fun PhaseLabel(phase: CallPhase, thinkingSince: Long?) {
    var elapsed by remember { mutableLongStateOf(0L) }
    LaunchedEffect(thinkingSince) {
        while (thinkingSince != null) {
            elapsed = (System.currentTimeMillis() - thinkingSince) / 1000
            delay(500)
        }
    }
    val text = if (phase == CallPhase.Thinking) "Thinking… ${elapsed}s" else phase.label
    Text(text, style = MaterialTheme.typography.titleMedium, color = ArtyColors.Text)
}

@Composable
private fun RoundButton(icon: ImageVector, label: String, background: Color, tint: Color, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.size(68.dp).clip(CircleShape).background(background).clickable(onClick = onClick),
            contentAlignment = Alignment.Center,
        ) { Icon(icon, contentDescription = label, tint = tint, modifier = Modifier.size(30.dp)) }
        Spacer(Modifier.height(8.dp))
        Text(label, style = MaterialTheme.typography.labelMedium, color = ArtyColors.TextDim)
    }
}
