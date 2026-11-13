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
    val Ink = Color(0xFF19191C)
    val Sunken = Color(0xFF121214)
    val Surface = Color(0xFF222226)
    val Raised = Color(0xFF2C2C31)
    val Text = Color(0xFFEEEEF0)
    val TextDim = Color(0xFFB4B4BD)
    val TextFaint = Color(0xFF9A9AA5)
    val Line = Text.copy(alpha = 0.10f)
    val LineStrong = Text.copy(alpha = 0.18f)
    val LineControl = Text.copy(alpha = 0.46f)
    val Fill = Text.copy(alpha = 0.05f)
    val FillHover = Text.copy(alpha = 0.07f)
    val FillPress = Text.copy(alpha = 0.12f)
    val Signal = Color(0xFFE5383B)
    val SignalText = Color(0xFFFF7A7C)
    val SignalSoft = Signal.copy(alpha = 0.14f)
    val SignalContainer = Color(0xFF3D2529)
    val OnSignal = Color.White
    val Caution = Color(0xFFF2B552)
    val Added = Color(0xFF5FD990)
    val Shadow = Color(0xFF000000)
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

fun Modifier.artyBackground(): Modifier = drawBehind { drawRect(ArtyColors.Ink) }

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
