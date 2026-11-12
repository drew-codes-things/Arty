package app.arty.core

import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals

class LectureFilesTest {
    @Test
    fun `a second recording in the same minute gets its own file`() {
        val folder = Files.createTempDirectory("lectures").toFile()
        assertEquals("lecture-20261112-0330.m4a", LectureFiles.unusedFile(folder, "20261112-0330").name)
        folder.resolve("lecture-20261112-0330.m4a").writeText("first")
        assertEquals("lecture-20261112-0330-2.m4a", LectureFiles.unusedFile(folder, "20261112-0330").name)
        folder.resolve("lecture-20261112-0330-2.m4a").writeText("second")
        assertEquals("lecture-20261112-0330-3.m4a", LectureFiles.unusedFile(folder, "20261112-0330").name)
        folder.deleteRecursively()
    }
}
