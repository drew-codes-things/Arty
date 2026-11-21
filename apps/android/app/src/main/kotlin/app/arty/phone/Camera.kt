package app.arty.phone

import android.content.Context
import android.net.Uri
import androidx.core.content.FileProvider
import app.arty.core.CameraPhoto
import java.io.File

private const val PHOTOS_AUTHORITY_SUFFIX = ".photos"

fun newCameraPhotoUri(context: Context): Uri {
    val folder = File(context.cacheDir, CameraPhoto.FOLDER).apply { mkdirs() }
    val now = System.currentTimeMillis()
    folder.listFiles()?.filter { CameraPhoto.isLeftOver(it.lastModified(), now) }?.forEach { it.delete() }
    val photo = File(folder, CameraPhoto.fileName(now))
    return FileProvider.getUriForFile(context, context.packageName + PHOTOS_AUTHORITY_SUFFIX, photo)
}
