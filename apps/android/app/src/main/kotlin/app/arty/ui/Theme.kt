package app.arty.ui

import android.provider.Settings
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.tween
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.arty.R

object ArtyColors {
    val Ink = Color(0xFF1B1918)
    val Sunken = Color(0xFF151413)
    val Surface = Color(0xFF24211F)
    val Raised = Color(0xFF2E2A28)
    val Text = Color(0xFFF1ECE7)
    val TextDim = Color(0xFFC0B8B0)
    val TextFaint = Color(0xFF9D948C)
    val Line = Text.copy(alpha = 0.10f)
    val LineStrong = Text.copy(alpha = 0.18f)
    val LineControl = Text.copy(alpha = 0.40f)
    val Fill = Text.copy(alpha = 0.05f)
    val FillHover = Text.copy(alpha = 0.08f)
    val FillPress = Text.copy(alpha = 0.12f)
    val Signal = Color(0xFFE5383B)
    val SignalText = Color(0xFFFF8A8C)
    val SignalSoft = Signal.copy(alpha = 0.15f)
    val SignalContainer = Color(0xFF412423)
    val OnSignal = Color.White
    val Caution = Color(0xFFF0A8A9)
    val Added = Color(0xFF7EE2A8)
    val Shadow = Color(0xFF080504)
}

object ArtyRadius {
    val Small = 8.dp
    val Medium = 12.dp
    val Large = 16.dp
    val ExtraLarge = 24.dp
}

val YourBubbleShape = RoundedCornerShape(
    topStart = ArtyRadius.ExtraLarge, topEnd = ArtyRadius.ExtraLarge, bottomEnd = ArtyRadius.Small, bottomStart = ArtyRadius.ExtraLarge,
)

object ArtyElevation {
    val Soft = 6.dp
    val Float = 16.dp
}

object ArtyMotion {
    const val QUICK_MS = 160
    const val RISE_MS = 240
    const val PRESS_SCALE_ICON = 0.94f
    const val PRESS_SCALE_BUTTON = 0.97f
    val RiseDistance = 8.dp
    val Ease = CubicBezierEasing(0.2f, 0.7f, 0.3f, 1f)

    fun <T> quick(): FiniteAnimationSpec<T> = tween(QUICK_MS, easing = Ease)

    fun <T> rise(): FiniteAnimationSpec<T> = tween(RISE_MS, easing = Ease)
}

private const val ANIMATIONS_OFF = 0f

fun isReducedMotion(animatorScale: Float): Boolean = animatorScale == ANIMATIONS_OFF

val LocalReducedMotion = staticCompositionLocalOf { false }

@Composable
private fun systemAsksForReducedMotion(): Boolean {
    val context = LocalContext.current
    return remember(context) {
        isReducedMotion(Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f))
    }
}

private val InkTop = Color(0xFF211E1C)
private val InkBottom = Color(0xFF171514)
private val Ember = Color(0xFFE57850)
private const val RED_GLOW_ALPHA = 0.16f
private const val EMBER_GLOW_ALPHA = 0.05f
private const val RED_GLOW_REACH = 0.65f
private const val EMBER_GLOW_REACH = 0.5f
private val InkFall = Brush.verticalGradient(0f to InkTop, 0.46f to ArtyColors.Ink, 1f to InkBottom)

fun Modifier.artyBackground(): Modifier = drawBehind {
    drawRect(InkFall)
    drawRect(
        Brush.radialGradient(
            listOf(ArtyColors.Signal.copy(alpha = RED_GLOW_ALPHA), Color.Transparent),
            center = Offset.Zero,
            radius = size.maxDimension * RED_GLOW_REACH,
        ),
    )
    drawRect(
        Brush.radialGradient(
            listOf(Ember.copy(alpha = EMBER_GLOW_ALPHA), Color.Transparent),
            center = Offset(size.width, size.height),
            radius = size.maxDimension * EMBER_GLOW_REACH,
        ),
    )
}

val PlexSans = FontFamily(
    Font(R.font.plex_sans_regular, FontWeight.Normal),
    Font(R.font.plex_sans_medium, FontWeight.Medium),
    Font(R.font.plex_sans_semibold, FontWeight.SemiBold),
)

val PlexMono = FontFamily(
    Font(R.font.plex_mono_regular, FontWeight.Normal),
    Font(R.font.plex_mono_medium, FontWeight.Medium),
    Font(R.font.plex_mono_semibold, FontWeight.SemiBold),
)

val KickerStyle = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Medium, fontSize = 12.sp, letterSpacing = 0.1.sp)

private val colors = darkColorScheme(
    primary = ArtyColors.Signal,
    onPrimary = ArtyColors.OnSignal,
    primaryContainer = ArtyColors.SignalContainer,
    onPrimaryContainer = ArtyColors.SignalText,
    inversePrimary = ArtyColors.SignalText,
    secondary = ArtyColors.TextDim,
    onSecondary = ArtyColors.Ink,
    secondaryContainer = ArtyColors.SignalContainer,
    onSecondaryContainer = ArtyColors.SignalText,
    tertiary = ArtyColors.Caution,
    onTertiary = ArtyColors.Ink,
    background = ArtyColors.Ink,
    onBackground = ArtyColors.Text,
    surface = ArtyColors.Ink,
    onSurface = ArtyColors.Text,
    surfaceVariant = ArtyColors.Raised,
    onSurfaceVariant = ArtyColors.TextDim,
    surfaceTint = ArtyColors.Text,
    inverseSurface = ArtyColors.Raised,
    inverseOnSurface = ArtyColors.Text,
    surfaceDim = ArtyColors.Sunken,
    surfaceBright = ArtyColors.Raised,
    surfaceContainerLowest = ArtyColors.Sunken,
    surfaceContainerLow = ArtyColors.Surface,
    surfaceContainer = ArtyColors.Surface,
    surfaceContainerHigh = ArtyColors.Surface,
    surfaceContainerHighest = ArtyColors.Raised,
    outline = ArtyColors.LineStrong,
    outlineVariant = ArtyColors.Line,
    error = ArtyColors.SignalText,
    onError = ArtyColors.Ink,
    errorContainer = ArtyColors.SignalContainer,
    onErrorContainer = ArtyColors.SignalText,
    scrim = ArtyColors.Shadow,
)

private val shapes = Shapes(
    extraSmall = RoundedCornerShape(ArtyRadius.Small),
    small = RoundedCornerShape(ArtyRadius.Medium),
    medium = RoundedCornerShape(ArtyRadius.Large),
    large = RoundedCornerShape(ArtyRadius.ExtraLarge),
    extraLarge = RoundedCornerShape(ArtyRadius.ExtraLarge),
)

private val base = Typography()
private val typography = Typography(
    headlineSmall = base.headlineSmall.copy(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.3).sp),
    titleLarge = base.titleLarge.copy(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, fontSize = 20.sp, letterSpacing = (-0.25).sp),
    titleMedium = base.titleMedium.copy(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.1).sp),
    titleSmall = base.titleSmall.copy(fontFamily = PlexSans, fontWeight = FontWeight.Medium),
    bodyLarge = base.bodyLarge.copy(fontFamily = PlexSans, fontSize = 16.sp, lineHeight = 25.sp),
    bodyMedium = base.bodyMedium.copy(fontFamily = PlexSans, fontSize = 14.sp, lineHeight = 22.sp),
    bodySmall = base.bodySmall.copy(fontFamily = PlexSans),
    labelLarge = base.labelLarge.copy(fontFamily = PlexSans, fontWeight = FontWeight.Medium),
    labelMedium = base.labelMedium.copy(fontFamily = PlexSans),
    labelSmall = base.labelSmall.copy(fontFamily = PlexSans),
)

@Composable
fun ArtyTheme(content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalReducedMotion provides systemAsksForReducedMotion()) {
        MaterialTheme(colorScheme = colors, shapes = shapes, typography = typography, content = content)
    }
}
