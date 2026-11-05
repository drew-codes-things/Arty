package app.arty.ui

import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.util.LruCache
import android.widget.MediaController
import android.widget.VideoView
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import app.arty.core.ArtyException
import app.arty.core.MediaKind
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException

interface ArtyMedia {
    suspend fun file(link: String): File
    fun save(link: String)
}

val LocalArtyMedia = staticCompositionLocalOf<ArtyMedia?> { null }

private const val PREVIEW_EDGE = 1440
private const val PREVIEWS_KEPT = 24
private const val DEFAULT_ASPECT = 16f / 9f
private val previews = LruCache<String, ImageBitmap>(PREVIEWS_KEPT)

private sealed interface Loaded {
    data object Waiting : Loaded
    data class Ready(val file: File, val preview: ImageBitmap?) : Loaded
    data class Failed(val reason: String) : Loaded
}

private fun decodePicture(file: File): ImageBitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.path, bounds)
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= PREVIEW_EDGE) sample *= 2
    return BitmapFactory.decodeFile(file.path, BitmapFactory.Options().apply { inSampleSize = sample })?.asImageBitmap()
}

private fun videoFrame(file: File): ImageBitmap? {
    val retriever = MediaMetadataRetriever()
    return try {
        retriever.setDataSource(file.path)
        retriever.getScaledFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC, PREVIEW_EDGE, PREVIEW_EDGE)?.asImageBitmap()
    } catch (error: RuntimeException) {
        null
    } finally {
        retriever.release()
    }
}

@Composable
fun MediaBlock(name: String, link: String, kind: MediaKind) {
    val media = LocalArtyMedia.current ?: return Text(name, color = ArtyColors.Signal)
    var loaded by remember(link) { mutableStateOf<Loaded>(Loaded.Waiting) }
    LaunchedEffect(link) {
        loaded = try {
            val file = media.file(link)
            val preview = previews.get(link) ?: withContext(Dispatchers.IO) {
                if (kind == MediaKind.Picture) decodePicture(file) else videoFrame(file)
            }?.also { previews.put(link, it) }
            Loaded.Ready(file, preview)
        } catch (error: ArtyException) {
            Loaded.Failed(error.message ?: "Arty couldn't send it")
        } catch (error: IOException) {
            Loaded.Failed(error.message ?: "the download failed")
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        when (val state = loaded) {
            Loaded.Waiting -> Box(
                Modifier.fillMaxWidth().height(180.dp).background(ArtyColors.Panel, RoundedCornerShape(8.dp)),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator(Modifier.size(28.dp), color = ArtyColors.Signal, strokeWidth = 2.dp) }
            is Loaded.Failed -> Text("Couldn't load $name: ${state.reason}", style = MaterialTheme.typography.bodyMedium, color = ArtyColors.Caution)
            is Loaded.Ready -> if (kind == MediaKind.Picture) Picture(name, state.preview) else Video(state.file, state.preview)
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(name, style = MaterialTheme.typography.labelMedium, color = ArtyColors.Muted, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            TextButton(onClick = { media.save(link) }) {
                Icon(ArtyIcons.Save, contentDescription = null, modifier = Modifier.size(16.dp), tint = ArtyColors.Signal)
                Text(" Save", color = ArtyColors.Signal)
            }
        }
    }
}

@Composable
private fun Picture(name: String, preview: ImageBitmap?) {
    if (preview == null) {
        Text("$name can't be shown here; save it to open it.", style = MaterialTheme.typography.bodyMedium, color = ArtyColors.Muted)
        return
    }
    var fullScreen by remember { mutableStateOf(false) }
    Image(
        preview, contentDescription = name, contentScale = ContentScale.Fit,
        modifier = Modifier.fillMaxWidth().heightIn(max = 420.dp).clip(RoundedCornerShape(8.dp)).clickable { fullScreen = true },
    )
    if (fullScreen) {
        Dialog(onDismissRequest = { fullScreen = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Box(Modifier.fillMaxSize().background(Color.Black).clickable { fullScreen = false }, contentAlignment = Alignment.Center) {
                Image(preview, contentDescription = name, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
            }
        }
    }
}

@Composable
private fun Video(file: File, preview: ImageBitmap?) {
    var playing by remember(file) { mutableStateOf(false) }
    val aspect = preview?.let { it.width.toFloat() / it.height } ?: DEFAULT_ASPECT
    val frame = Modifier.fillMaxWidth().aspectRatio(aspect).clip(RoundedCornerShape(8.dp)).background(Color.Black)
    if (playing) {
        AndroidView(
            factory = { context ->
                VideoView(context).apply {
                    setVideoPath(file.path)
                    setMediaController(MediaController(context).also { it.setAnchorView(this) })
                    setOnPreparedListener { start() }
                }
            },
            onRelease = { it.stopPlayback() },
            modifier = frame,
        )
        return
    }
    Box(frame.clickable { playing = true }, contentAlignment = Alignment.Center) {
        if (preview != null) Image(preview, contentDescription = null, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
        Box(Modifier.size(56.dp).background(Color.Black.copy(alpha = 0.6f), CircleShape), contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.PlayArrow, contentDescription = "Play", tint = Color.White, modifier = Modifier.size(36.dp).padding(2.dp))
        }
    }
}
