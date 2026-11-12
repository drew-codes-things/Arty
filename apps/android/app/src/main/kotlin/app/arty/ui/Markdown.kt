package app.arty.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.arty.core.MediaKind
import app.arty.core.mediaKind

/** Just enough Markdown for chat replies: headings, lists, code blocks, bold, italic, inline code and links. */
private sealed interface Block {
    data class Paragraph(val text: String) : Block
    data class Heading(val text: String) : Block
    data class Bullet(val marker: String, val text: String) : Block
    data class Code(val text: String) : Block
    /** "> " lines: a draft for the user to copy (a post, an email). Shown in a panel, not read aloud. */
    data class Quote(val text: String) : Block
    data class Media(val name: String, val link: String, val kind: MediaKind) : Block
}

private val BULLET = Regex("""^\s*([-*•]|\d+[.)])\s+(.*)$""")
private val HEADING = Regex("""^\s*#{1,6}\s+(.*)$""")
private val INLINE = Regex("""\*\*(.+?)\*\*|`([^`]+)`|!?\[([^\]]+)]\(([^)]+)\)|(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])""")
private val MEDIA_LINE = Regex("""^\s*(!?)\[([^\]]*)]\(([^)\s]*/arty-files/[^)\s]+)\)\s*$""")

private fun parse(markdown: String): List<Block> {
    val blocks = mutableListOf<Block>()
    val paragraph = StringBuilder()
    fun flush() {
        if (paragraph.isNotBlank()) blocks += Block.Paragraph(paragraph.toString().trim())
        paragraph.clear()
    }
    val lines = markdown.lines()
    var index = 0
    while (index < lines.size) {
        val line = lines[index]
        val media = mediaOf(line)
        when {
            line.trimStart().startsWith("```") -> {
                flush()
                val code = StringBuilder()
                index++
                while (index < lines.size && !lines[index].trimStart().startsWith("```")) {
                    code.appendLine(lines[index]); index++
                }
                blocks += Block.Code(code.toString().trimEnd())
            }
            line.trimStart().startsWith(">") -> {
                flush()
                val quote = StringBuilder()
                while (index < lines.size && lines[index].trimStart().startsWith(">")) {
                    quote.appendLine(lines[index].trimStart().removePrefix(">").removePrefix(" ")); index++
                }
                index-- // the loop below moves on past the last quoted line
                blocks += Block.Quote(quote.toString().trim())
            }
            media != null -> { flush(); blocks += media }
            line.isBlank() -> flush()
            HEADING.matches(line) -> { flush(); blocks += Block.Heading(HEADING.find(line)!!.groupValues[1]) }
            BULLET.matches(line) -> {
                flush()
                val match = BULLET.find(line)!!
                val marker = match.groupValues[1].let { if (it.first().isDigit()) it else "•" }
                blocks += Block.Bullet(marker, match.groupValues[2])
            }
            else -> paragraph.append(if (paragraph.isEmpty()) line else "\n$line")
        }
        index++
    }
    flush()
    return blocks
}

private fun mediaOf(line: String): Block.Media? {
    val (picture, name, link) = MEDIA_LINE.matchEntire(line)?.destructured ?: return null
    val kind = mediaKind(link, picture.isNotEmpty()) ?: return null
    return Block.Media(name.ifBlank { "file" }, link, kind)
}

private fun inline(text: String): AnnotatedString = buildAnnotatedString {
    var last = 0
    for (match in INLINE.findAll(text)) {
        append(text.substring(last, match.range.first))
        val (bold, code, linkText, linkUrl, italic) = match.destructured
        when {
            bold.isNotEmpty() -> pushStyle(SpanStyle(fontWeight = FontWeight.SemiBold)).also { append(bold) }
            code.isNotEmpty() -> pushStyle(SpanStyle(fontFamily = PlexMono, fontSize = 13.sp, background = ArtyColors.Raised)).also { append(code) }
            linkText.isNotEmpty() -> {
                // Tapping opens it through LocalUriHandler, which the chat screen uses to catch Arty's file links.
                withLink(LinkAnnotation.Url(linkUrl, TextLinkStyles(SpanStyle(color = ArtyColors.SignalText, textDecoration = TextDecoration.Underline)))) {
                    append(linkText)
                }
                last = match.range.last + 1
                continue
            }
            else -> pushStyle(SpanStyle(fontStyle = FontStyle.Italic)).also { append(italic) }
        }
        pop()
        last = match.range.last + 1
    }
    append(text.substring(last))
}

/** Plain text for reading aloud: no symbols, no code, and no quoted draft (it's there to copy, not to hear). */
fun speakableText(markdown: String): String =
    markdown.replace(Regex("""^\s*>.*$\n?""", RegexOption.MULTILINE), "")
        .replace(Regex("```[\\s\\S]*?```"), " (code) ")
        .replace(Regex("""!\[[^\]]*]\([^)]+\)"""), "")
        .replace(Regex("""\[([^\]]+)]\([^)]+\)"""), "$1")
        .replace(Regex("[*`#_>]"), "")
        .replace(Regex("""^\s*[-•]\s+""", RegexOption.MULTILINE), "")
        .trim()

@Composable
fun MarkdownText(markdown: String, modifier: Modifier = Modifier) {
    val blocks = remember(markdown) { parse(markdown) }
    val body = MaterialTheme.typography.bodyLarge
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (block in blocks) {
            when (block) {
                is Block.Paragraph -> Text(inline(block.text), style = body)
                is Block.Heading -> Text(inline(block.text), style = MaterialTheme.typography.titleSmall, color = ArtyColors.Caution)
                is Block.Bullet -> Row {
                    Text(block.marker, style = body, color = ArtyColors.Signal, modifier = Modifier.width(22.dp))
                    Text(inline(block.text), style = body)
                }
                is Block.Quote -> Row(
                    Modifier.fillMaxWidth().height(IntrinsicSize.Min)
                        .clip(MaterialTheme.shapes.extraSmall).background(ArtyColors.Surface),
                ) {
                    Box(Modifier.width(3.dp).fillMaxHeight().background(ArtyColors.Signal))
                    Text(inline(block.text), style = body, modifier = Modifier.padding(12.dp))
                }
                is Block.Code -> Text(
                    block.text,
                    fontFamily = PlexMono, fontSize = 13.sp, lineHeight = 19.sp, color = ArtyColors.Text,
                    softWrap = false,
                    modifier = Modifier.fillMaxWidth()
                        .clip(MaterialTheme.shapes.small).background(ArtyColors.Sunken)
                        .border(1.dp, ArtyColors.Line, MaterialTheme.shapes.small)
                        .horizontalScroll(rememberScrollState())
                        .padding(12.dp),
                )
                is Block.Media -> MediaBlock(block.name, block.link, block.kind)
            }
        }
    }
}
