package app.arty.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

private const val BODY_TEXT_CONTRAST = 4.5
private const val CONTROL_CONTRAST = 3.0

private fun channel(value: Float): Double =
    if (value <= 0.03928f) value / 12.92 else ((value + 0.055) / 1.055).pow(2.4)

private fun luminance(colour: Color): Double =
    0.2126 * channel(colour.red) + 0.7152 * channel(colour.green) + 0.0722 * channel(colour.blue)

private fun contrast(foreground: Color, background: Color): Double {
    val lighter = max(luminance(foreground), luminance(background))
    val darker = min(luminance(foreground), luminance(background))
    return (lighter + 0.05) / (darker + 0.05)
}

class WarmTokensTest {
    private val surfaces = listOf(ArtyColors.Ink, ArtyColors.Sunken, ArtyColors.Surface, ArtyColors.Raised)
    private val textTones = listOf(ArtyColors.Text, ArtyColors.TextDim, ArtyColors.TextFaint)

    @Test
    fun everyTextToneIsReadableOnEverySurface() {
        for (surface in surfaces) for (tone in textTones) {
            val ratio = contrast(tone, surface)
            assertTrue(ratio >= BODY_TEXT_CONTRAST, "text $tone on $surface is only $ratio:1")
        }
    }

    @Test
    fun redTextIsReadableOnSurfacesAndOnTheRedTint() {
        for (surface in surfaces) {
            assertTrue(contrast(ArtyColors.SignalText, surface) >= BODY_TEXT_CONTRAST)
            assertTrue(contrast(ArtyColors.SignalText, ArtyColors.SignalSoft.compositeOver(surface)) >= BODY_TEXT_CONTRAST)
        }
    }

    @Test
    fun redButtonLabelsStandOutFromTheRed() {
        assertTrue(contrast(ArtyColors.OnSignal, ArtyColors.Signal) >= CONTROL_CONTRAST)
    }

    @Test
    fun linesAndFillsAreMixedFromTheTextColour() {
        for (mixed in listOf(ArtyColors.Line, ArtyColors.LineStrong, ArtyColors.LineControl, ArtyColors.Fill, ArtyColors.FillHover, ArtyColors.FillPress)) {
            assertEquals(ArtyColors.Text.copy(alpha = mixed.alpha), mixed)
        }
    }

    @Test
    fun theOpaqueRedContainerMatchesTheTintOverTheSurface() {
        val tinted = ArtyColors.SignalSoft.compositeOver(ArtyColors.Surface)
        val container = ArtyColors.SignalContainer
        for ((expected, actual) in listOf(tinted.red to container.red, tinted.green to container.green, tinted.blue to container.blue)) {
            assertEquals(expected, actual, 1f / 255f)
        }
    }
}

class ArrivalsTest {
    @Test
    fun aKeyIsNewOnlyTheFirstTimeItIsSeen() {
        val arrivals = Arrivals()
        assertTrue(arrivals.isNew("a"))
        assertFalse(arrivals.isNew("a"))
        assertTrue(arrivals.isNew("b"))
    }

    @Test
    fun aKeyMarkedAheadOfTimeIsNotNew() {
        val arrivals = Arrivals()
        arrivals.markSeen("reply")
        assertFalse(arrivals.isNew("reply"))
    }
}

class ReducedMotionTest {
    @Test
    fun aZeroAnimatorScaleMeansReducedMotion() {
        assertTrue(isReducedMotion(0f))
    }

    @Test
    fun anyOtherAnimatorScaleKeepsMotion() {
        assertFalse(isReducedMotion(1f))
        assertFalse(isReducedMotion(0.5f))
        assertFalse(isReducedMotion(10f))
    }
}
