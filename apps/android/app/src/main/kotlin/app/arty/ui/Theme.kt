package app.arty.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.foundation.background
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import app.arty.R

object ArtyColors {
    val Void = Color(0xFF0A0A0B)
    val Panel = Color(0xFF141416)
    val PanelRaised = Color(0xFF1E1E21)
    val Line = Color(0x14FFFFFF)
    val LineBright = Color(0x24FFFFFF)
    val Signal = Color(0xFFE5383B)
    val SignalDeep = Color(0xFF7A0F14)
    val Muted = Color(0xFFA1A1A6)
    val Caution = Color(0xFFF0A8A9)
    val Text = Color(0xFFECECEC)
    val TextDim = Color(0xFFA1A1A6)
    val TextFaint = Color(0xFF6B6B72)
}

val ArtyBackground = Brush.verticalGradient(listOf(Color(0xFF2A080C), Color(0xFF120B0C), Color(0xFF0A0A0B), Color(0xFF070708)))
val ArtyRedGradient = Brush.linearGradient(listOf(ArtyColors.Signal, ArtyColors.SignalDeep))

fun Modifier.artyBackground(): Modifier = background(ArtyBackground)

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
    onPrimary = Color.White,
    secondary = ArtyColors.Muted,
    onSecondary = ArtyColors.Void,
    tertiary = ArtyColors.Caution,
    background = ArtyColors.Void,
    onBackground = ArtyColors.Text,
    surface = ArtyColors.Panel,
    onSurface = ArtyColors.Text,
    surfaceVariant = ArtyColors.PanelRaised,
    onSurfaceVariant = ArtyColors.TextDim,
    surfaceContainer = ArtyColors.Panel,
    surfaceContainerHigh = ArtyColors.PanelRaised,
    surfaceContainerHighest = ArtyColors.PanelRaised,
    surfaceContainerLow = ArtyColors.Panel,
    outline = ArtyColors.LineBright,
    outlineVariant = ArtyColors.Line,
    error = ArtyColors.Signal,
)

private val base = Typography()
private val typography = Typography(
    headlineSmall = base.headlineSmall.copy(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold),
    titleLarge = base.titleLarge.copy(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, fontSize = 20.sp),
    titleMedium = base.titleMedium.copy(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold),
    titleSmall = base.titleSmall.copy(fontFamily = PlexSans, fontWeight = FontWeight.Medium),
    bodyLarge = base.bodyLarge.copy(fontFamily = PlexSans, fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = base.bodyMedium.copy(fontFamily = PlexSans, fontSize = 14.sp, lineHeight = 21.sp),
    bodySmall = base.bodySmall.copy(fontFamily = PlexSans),
    labelLarge = base.labelLarge.copy(fontFamily = PlexSans, fontWeight = FontWeight.Medium),
    labelMedium = base.labelMedium.copy(fontFamily = PlexSans),
    labelSmall = base.labelSmall.copy(fontFamily = PlexSans),
)

@Composable
fun ArtyTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = colors, typography = typography, content = content)
}
