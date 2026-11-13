package app.arty.ui

import android.os.SystemClock
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.arty.core.AnswerGuard
import kotlinx.coroutines.delay

@Stable
class AnswerHold {
    private val guard = AnswerGuard()
    var seen by mutableStateOf(false)
        private set
    var ready by mutableStateOf(false)
        private set

    fun see(now: Long) {
        guard.see(now)
        seen = true
    }

    fun millisLeft(now: Long): Long = guard.millisLeft(now)

    fun check(now: Long) {
        ready = guard.isReady(now)
    }
}

@Composable
fun rememberAnswerHold(key: Any): AnswerHold {
    val hold = remember(key) { AnswerHold() }
    LaunchedEffect(hold, hold.seen) {
        if (!hold.seen) return@LaunchedEffect
        delay(hold.millisLeft(SystemClock.uptimeMillis()))
        hold.check(SystemClock.uptimeMillis())
    }
    return hold
}

fun Modifier.seenFor(hold: AnswerHold): Modifier = onGloballyPositioned { coordinates ->
    val onScreen = coordinates.boundsInWindow()
    if (onScreen.width > 0f && onScreen.height > 0f) hold.see(SystemClock.uptimeMillis())
}

@Composable
fun HeldQuestion(hold: AnswerHold, modifier: Modifier = Modifier, card: @Composable BoxScope.() -> Unit) {
    Box(modifier.seenFor(hold)) {
        card()
        AnimatedVisibility(
            visible = !hold.ready,
            enter = fadeIn(ArtyMotion.quick()),
            exit = fadeOut(ArtyMotion.quick()),
            modifier = Modifier.align(Alignment.TopEnd).padding(8.dp),
        ) {
            Text(
                AnswerGuard.NEW_QUESTION,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.SemiBold,
                color = ArtyColors.OnSignal,
                modifier = Modifier.background(ArtyColors.Signal, MaterialTheme.shapes.extraLarge)
                    .padding(horizontal = 8.dp, vertical = 2.dp),
            )
        }
    }
}
