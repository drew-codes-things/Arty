package app.arty.core

import java.io.File

object LectureFiles {
    private const val PREFIX = "lecture-"
    private const val EXTENSION = ".m4a"
    private const val FIRST_REPEAT = 2

    fun unusedFile(folder: File, stamp: String): File {
        val first = File(folder, "$PREFIX$stamp$EXTENSION")
        if (!first.exists()) return first
        return generateSequence(FIRST_REPEAT) { it + 1 }
            .map { File(folder, "$PREFIX$stamp-$it$EXTENSION") }
            .first { !it.exists() }
    }
}
