package app.arty.core

enum class MediaKind { Picture, Video }

private val PICTURE_EXTENSIONS = setOf("png", "jpg", "jpeg", "webp", "gif", "bmp", "heic", "avif")
private val VIDEO_EXTENSIONS = setOf("mp4", "webm", "mov", "mkv", "3gp")

fun mediaKind(link: String, markedPicture: Boolean): MediaKind? {
    val extension = link.substringBefore('?').substringAfterLast('.', "").lowercase()
    return when {
        extension in VIDEO_EXTENSIONS -> MediaKind.Video
        markedPicture || extension in PICTURE_EXTENSIONS -> MediaKind.Picture
        else -> null
    }
}
