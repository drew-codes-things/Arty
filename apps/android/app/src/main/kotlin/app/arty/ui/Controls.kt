package app.arty.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.lazy.LazyItemScope
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp

class Arrivals {
    private val seen = HashSet<Any>()

    fun isNew(key: Any): Boolean = seen.add(key)

    fun markSeen(key: Any) {
        seen.add(key)
    }
}

@Composable
fun rememberArrivals(): Arrivals = remember { Arrivals() }

fun Modifier.riseIn(arrivals: Arrivals, key: Any): Modifier = composed {
    val still = LocalReducedMotion.current
    val fresh = remember(key) { arrivals.isNew(key) && !still }
    val progress = remember(key) { Animatable(if (fresh) 0f else 1f) }
    LaunchedEffect(key) { progress.animateTo(1f, ArtyMotion.rise()) }
    val distance = with(LocalDensity.current) { ArtyMotion.RiseDistance.toPx() }
    graphicsLayer {
        alpha = progress.value
        translationY = (1f - progress.value) * distance
    }
}

fun Modifier.arrival(scope: LazyItemScope, arrivals: Arrivals, key: Any): Modifier =
    with(scope) { animateItem(fadeInSpec = null, placementSpec = ArtyMotion.quick(), fadeOutSpec = ArtyMotion.quick()) }
        .riseIn(arrivals, key)

fun Modifier.pressScale(source: InteractionSource, pressedScale: Float = ArtyMotion.PRESS_SCALE_BUTTON): Modifier = composed {
    val pressed by source.collectIsPressedAsState()
    val still = LocalReducedMotion.current
    val scale by animateFloatAsState(if (pressed && !still) pressedScale else 1f, ArtyMotion.quick(), label = "press")
    graphicsLayer {
        scaleX = scale
        scaleY = scale
    }
}

fun Modifier.softShadow(shape: Shape): Modifier =
    shadow(ArtyElevation.Soft, shape, clip = false, ambientColor = ArtyColors.Shadow, spotColor = ArtyColors.Shadow)

fun Modifier.floatShadow(shape: Shape): Modifier =
    shadow(ArtyElevation.Float, shape, clip = false, ambientColor = ArtyColors.Shadow, spotColor = ArtyColors.Shadow)

@Composable
fun ArtyButton(onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, content: @Composable RowScope.() -> Unit) {
    val source = remember { MutableInteractionSource() }
    Button(
        onClick = onClick,
        enabled = enabled,
        interactionSource = source,
        colors = ButtonDefaults.buttonColors(
            containerColor = ArtyColors.Signal, contentColor = ArtyColors.OnSignal,
            disabledContainerColor = ArtyColors.FillPress, disabledContentColor = ArtyColors.TextFaint,
        ),
        modifier = modifier.pressScale(source),
        content = content,
    )
}

@Composable
fun ArtyQuietButton(onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, content: @Composable RowScope.() -> Unit) {
    val source = remember { MutableInteractionSource() }
    Button(
        onClick = onClick,
        enabled = enabled,
        interactionSource = source,
        colors = ButtonDefaults.buttonColors(
            containerColor = ArtyColors.Raised, contentColor = ArtyColors.Text,
            disabledContainerColor = ArtyColors.Fill, disabledContentColor = ArtyColors.TextFaint,
        ),
        modifier = modifier.pressScale(source),
        content = content,
    )
}

@Composable
fun ArtyOutlinedButton(onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, content: @Composable RowScope.() -> Unit) {
    val source = remember { MutableInteractionSource() }
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        interactionSource = source,
        border = BorderStroke(1.dp, if (enabled) ArtyColors.LineStrong else ArtyColors.Line),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = ArtyColors.Text, disabledContentColor = ArtyColors.TextFaint),
        modifier = modifier.pressScale(source),
        content = content,
    )
}

@Composable
fun ArtyTextButton(label: String, onClick: () -> Unit, modifier: Modifier = Modifier, colour: Color = ArtyColors.SignalText, enabled: Boolean = true) {
    val source = remember { MutableInteractionSource() }
    TextButton(
        onClick = onClick,
        enabled = enabled,
        interactionSource = source,
        colors = ButtonDefaults.textButtonColors(contentColor = colour, disabledContentColor = ArtyColors.TextFaint),
        modifier = modifier.pressScale(source),
    ) { Text(label) }
}

@Composable
fun ArtyIconButton(onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, content: @Composable () -> Unit) {
    val source = remember { MutableInteractionSource() }
    IconButton(
        onClick = onClick,
        enabled = enabled,
        interactionSource = source,
        modifier = modifier.pressScale(source, ArtyMotion.PRESS_SCALE_ICON),
        content = content,
    )
}

@Composable
fun artyFieldColors(container: Color = ArtyColors.Sunken): TextFieldColors = OutlinedTextFieldDefaults.colors(
    focusedTextColor = ArtyColors.Text,
    unfocusedTextColor = ArtyColors.Text,
    disabledTextColor = ArtyColors.TextFaint,
    focusedContainerColor = container,
    unfocusedContainerColor = container,
    disabledContainerColor = container,
    cursorColor = ArtyColors.Signal,
    focusedBorderColor = ArtyColors.SignalText,
    unfocusedBorderColor = ArtyColors.LineStrong,
    disabledBorderColor = ArtyColors.Line,
    focusedLabelColor = ArtyColors.SignalText,
    unfocusedLabelColor = ArtyColors.TextFaint,
    focusedPlaceholderColor = ArtyColors.TextFaint,
    unfocusedPlaceholderColor = ArtyColors.TextFaint,
    disabledPlaceholderColor = ArtyColors.TextFaint,
)
