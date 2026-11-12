package app.arty.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

/** The few icons missing from material-icons-core, drawn as simple 24dp line icons
 *  (the extended icon set adds megabytes for four glyphs). */
object ArtyIcons {
    private fun lineIcon(name: String, block: androidx.compose.ui.graphics.vector.PathBuilder.() -> Unit) =
        ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).path(
            stroke = SolidColor(Color.White), strokeLineWidth = 2f,
            strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round, pathBuilder = block,
        ).build()

    val Book = lineIcon("book") {
        moveTo(12f, 7.5f); curveTo(12f, 6.1f, 10.9f, 5f, 9.5f, 5f); lineTo(3f, 5f); lineTo(3f, 17.5f); lineTo(10f, 17.5f)
        curveTo(11.1f, 17.5f, 12f, 18.4f, 12f, 19.5f); close()
        moveTo(12f, 7.5f); curveTo(12f, 6.1f, 13.1f, 5f, 14.5f, 5f); lineTo(21f, 5f); lineTo(21f, 17.5f); lineTo(14f, 17.5f)
        curveTo(12.9f, 17.5f, 12f, 18.4f, 12f, 19.5f); close()
    }

    val Mic = lineIcon("mic") {
        moveTo(12f, 3f); curveTo(10.3f, 3f, 9f, 4.3f, 9f, 6f); lineTo(9f, 12f)
        curveTo(9f, 13.7f, 10.3f, 15f, 12f, 15f); curveTo(13.7f, 15f, 15f, 13.7f, 15f, 12f)
        lineTo(15f, 6f); curveTo(15f, 4.3f, 13.7f, 3f, 12f, 3f); close()
        moveTo(5.5f, 11.5f); curveTo(5.5f, 15.1f, 8.4f, 18f, 12f, 18f); curveTo(15.6f, 18f, 18.5f, 15.1f, 18.5f, 11.5f)
        moveTo(12f, 18f); lineTo(12f, 21f)
    }

    val Speaker = lineIcon("speaker") {
        moveTo(4f, 9.5f); lineTo(4f, 14.5f); lineTo(8f, 14.5f); lineTo(13f, 19f); lineTo(13f, 5f); lineTo(8f, 9.5f); close()
        moveTo(16.5f, 9f); curveTo(17.4f, 9.8f, 17.9f, 10.9f, 17.9f, 12f); curveTo(17.9f, 13.1f, 17.4f, 14.2f, 16.5f, 15f)
        moveTo(19f, 6.5f); curveTo(20.6f, 8f, 21.5f, 9.9f, 21.5f, 12f); curveTo(21.5f, 14.1f, 20.6f, 16f, 19f, 17.5f)
    }

    val Chat = lineIcon("chat") {
        moveTo(4f, 5f); lineTo(20f, 5f); lineTo(20f, 16f); lineTo(10f, 16f); lineTo(6f, 20f); lineTo(6f, 16f); lineTo(4f, 16f); close()
    }

    val Tasks = lineIcon("tasks") {
        moveTo(9f, 6f); lineTo(20f, 6f); moveTo(9f, 12f); lineTo(20f, 12f); moveTo(9f, 18f); lineTo(20f, 18f)
        moveTo(4f, 6f); lineTo(5f, 7f); lineTo(6.5f, 5f); moveTo(4f, 12f); lineTo(5f, 13f); lineTo(6.5f, 11f)
        moveTo(4f, 18f); lineTo(5f, 19f); lineTo(6.5f, 17f)
    }

    val Bell = lineIcon("bell") {
        moveTo(6f, 16.5f); lineTo(6f, 11f); curveTo(6f, 7.7f, 8.7f, 5f, 12f, 5f); curveTo(15.3f, 5f, 18f, 7.7f, 18f, 11f)
        lineTo(18f, 16.5f); lineTo(19.5f, 18f); lineTo(4.5f, 18f); close()
        moveTo(10f, 20.5f); curveTo(10.5f, 21.2f, 11.2f, 21.5f, 12f, 21.5f); curveTo(12.8f, 21.5f, 13.5f, 21.2f, 14f, 20.5f)
        moveTo(12f, 3f); lineTo(12f, 5f)
    }

    val Clock = lineIcon("clock") {
        moveTo(12f, 3.5f); curveTo(16.7f, 3.5f, 20.5f, 7.3f, 20.5f, 12f); curveTo(20.5f, 16.7f, 16.7f, 20.5f, 12f, 20.5f)
        curveTo(7.3f, 20.5f, 3.5f, 16.7f, 3.5f, 12f); curveTo(3.5f, 7.3f, 7.3f, 3.5f, 12f, 3.5f); close()
        moveTo(12f, 7.5f); lineTo(12f, 12f); lineTo(15f, 14f)
    }

    val Attach = lineIcon("attach") {
        moveTo(20f, 11.5f); lineTo(12.2f, 19.3f); curveTo(10.2f, 21.3f, 7f, 21.3f, 5f, 19.3f); curveTo(3f, 17.3f, 3f, 14.1f, 5f, 12.1f)
        lineTo(12.9f, 4.2f); curveTo(14.3f, 2.8f, 16.5f, 2.8f, 17.9f, 4.2f); curveTo(19.3f, 5.6f, 19.3f, 7.8f, 17.9f, 9.2f)
        lineTo(10f, 17.1f); curveTo(9.3f, 17.8f, 8.2f, 17.8f, 7.5f, 17.1f); curveTo(6.8f, 16.4f, 6.8f, 15.3f, 7.5f, 14.6f); lineTo(14.8f, 7.3f)
    }

    val Call = lineIcon("call") {
        moveTo(5f, 4f); lineTo(9f, 4f); lineTo(11f, 9f); lineTo(8.5f, 10.5f); curveTo(9.6f, 12.7f, 11.3f, 14.4f, 13.5f, 15.5f)
        lineTo(15f, 13f); lineTo(20f, 15f); lineTo(20f, 19f); curveTo(20f, 20.1f, 19.1f, 21f, 18f, 21f); curveTo(10.3f, 20.5f, 3.5f, 13.7f, 3f, 6f)
        curveTo(3f, 4.9f, 3.9f, 4f, 5f, 4f); close()
    }

    val HangUp = lineIcon("hang-up") {
        moveTo(3f, 14.5f); curveTo(8f, 9.5f, 16f, 9.5f, 21f, 14.5f); lineTo(19f, 17f); lineTo(15.5f, 15.5f); lineTo(15.5f, 13f)
        curveTo(13.2f, 12.3f, 10.8f, 12.3f, 8.5f, 13f); lineTo(8.5f, 15.5f); lineTo(5f, 17f); close()
    }

    val Calendar = lineIcon("calendar") {
        moveTo(5f, 5.5f); lineTo(19f, 5.5f); lineTo(19f, 20f); lineTo(5f, 20f); close()
        moveTo(5f, 10f); lineTo(19f, 10f); moveTo(9f, 3.5f); lineTo(9f, 7.5f); moveTo(15f, 3.5f); lineTo(15f, 7.5f)
    }

    val Brain = lineIcon("memory") {
        moveTo(12f, 5f); curveTo(10f, 3.5f, 6.5f, 4.5f, 6.5f, 7.5f); curveTo(4.5f, 8f, 4f, 11f, 5.5f, 12f)
        curveTo(4f, 13.5f, 5f, 16.5f, 7.5f, 16.5f); curveTo(8f, 19f, 11f, 19.5f, 12f, 18f); lineTo(12f, 5f)
        moveTo(12f, 5f); curveTo(14f, 3.5f, 17.5f, 4.5f, 17.5f, 7.5f); curveTo(19.5f, 8f, 20f, 11f, 18.5f, 12f)
        curveTo(20f, 13.5f, 19f, 16.5f, 16.5f, 16.5f); curveTo(16f, 19f, 13f, 19.5f, 12f, 18f)
    }

    val More = lineIcon("more") {
        moveTo(5f, 12f); lineTo(5.01f, 12f); moveTo(12f, 12f); lineTo(12.01f, 12f); moveTo(19f, 12f); lineTo(19.01f, 12f)
    }

    val Chevron = lineIcon("chevron") {
        moveTo(9f, 6f); lineTo(15f, 12f); lineTo(9f, 18f)
    }

    val Computer = lineIcon("computer") {
        moveTo(4f, 5f); lineTo(20f, 5f); lineTo(20f, 16f); lineTo(4f, 16f); close()
        moveTo(9f, 20f); lineTo(15f, 20f); moveTo(12f, 16f); lineTo(12f, 20f)
        moveTo(7.5f, 9f); lineTo(9.5f, 10.5f); lineTo(7.5f, 12f); moveTo(11f, 12f); lineTo(14f, 12f)
    }

    val Save = lineIcon("save") {
        moveTo(12f, 4f); lineTo(12f, 15f); moveTo(7f, 10f); lineTo(12f, 15f); lineTo(17f, 10f)
        moveTo(5f, 20f); lineTo(19f, 20f)
    }
}
