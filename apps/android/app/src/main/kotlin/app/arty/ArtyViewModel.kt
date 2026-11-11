package app.arty

import android.app.Application
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.webkit.MimeTypeMap
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.arty.core.Announcement
import app.arty.core.Attachment
import app.arty.core.ArtyException
import app.arty.core.ArtyTask
import app.arty.core.CalendarEvent
import app.arty.core.Lecture
import app.arty.lectures.LectureRecorder
import app.arty.core.ChatMessage
import app.arty.core.ChatWait
import app.arty.core.MemoryEntity
import app.arty.core.ModelOption
import app.arty.core.Schedule
import app.arty.core.batteryNote
import app.arty.core.recentWindow
import app.arty.core.withDeviceNote
import app.arty.data.Conversation
import app.arty.data.ConversationStore
import app.arty.data.StoredMessage
import app.arty.notify.Announcements
import app.arty.notify.LiveConnection
import app.arty.phone.PhoneAction
import app.arty.core.ArtyClient
import app.arty.ui.ArtyMedia
import app.arty.ui.speakableText
import app.arty.widget.ArtyWidget
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.net.URLDecoder
import java.security.MessageDigest
import java.util.Locale
import java.util.UUID

/** A file picked for the next message. [attachment] is set once it has uploaded; [error] if that failed. */
data class PendingFile(val key: String, val name: String, val attachment: Attachment? = null, val error: String? = null) {
    val uploading: Boolean get() = attachment == null && error == null
}

/** Everything the screens show, and every action they can take. Network calls run on the IO dispatcher. */
class ArtyViewModel(application: Application) : AndroidViewModel(application) {
    private companion object {
        const val HISTORY_MESSAGES = 14 // sent with each message: 14 to 21 (see recentWindow)
        const val HISTORY_STEP = 8
        const val POLL_MS = 1_500L
        const val GIVE_UP_MS = 20 * 60 * 1000L
        const val PREFS = "arty"
        const val PREF_AUTO_SPEAK = "autoSpeak"
        const val PREF_LAST_CHAT = "lastChat"
        const val ANNOUNCEMENT_WAIT_SECONDS = 50
        const val CALENDAR_DAYS = 60
        const val ANNOUNCEMENT_RETRY_MS = 30_000L
        const val PREF_LAST_SEEN_UPDATE = "lastSeenUpdate"
        const val MAX_UPDATES = 100
        const val MEDIA_FOLDER = "arty-media"
        const val MEDIA_CACHE_BYTES = 300L * 1024 * 1024
        const val CALL_INSTRUCTIONS = "The user is on a voice call with you and hears your reply read aloud. Answer in one to three short, " +
            "natural spoken sentences. No lists, headings, markdown, emoji or links unless they ask for them."
    }

    private val client = newArtyClient(application)
    private val store = ConversationStore(application.filesDir)
    private val prefs = application.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    var conversations by mutableStateOf<List<Conversation>>(emptyList()); private set
    var current by mutableStateOf(Conversation()); private set
    var models by mutableStateOf<List<ModelOption>>(emptyList()); private set
    var thinkingSince by mutableStateOf<Long?>(null); private set
    /** The reply's first sentences, shown (and read out, when replies are spoken) while Arty writes the rest. */
    var partialReply by mutableStateOf<String?>(null); private set
    private var spokenEarly = ""
    var autoSpeak by mutableStateOf(prefs.getBoolean(PREF_AUTO_SPEAK, false)); private set
    var draft by mutableStateOf("")
    var notice by mutableStateOf<String?>(null)
    /** A saved download the notice can offer to open. */
    var noticeFile by mutableStateOf<Uri?>(null)
    val pendingFiles = mutableStateListOf<PendingFile>()
    /** A call or text to open now (see PhoneActionHandler): from a reply that just came in, or its button. */
    var phoneRequest by mutableStateOf<PhoneAction?>(null); private set

    fun openPhoneAction(action: PhoneAction) {
        phoneRequest = action
    }

    fun phoneRequestHandled() {
        phoneRequest = null
    }

    /** Bumped to bring the Chat tab to the front (something was shared into Arty). */
    var chatRequests by mutableStateOf(0); private set

    fun showChat() {
        chatRequests++
    }

    var tasksRequests by mutableStateOf(0); private set
    /** Bumped to start listening for a message (the widget's Talk button). */
    var voiceRequests by mutableStateOf(0); private set

    fun showTasks() {
        tasksRequests++
        refreshTasks()
    }

    fun requestVoice() {
        showChat()
        voiceRequests++
    }

    private var following: Job? = null
    var instantNotifications by mutableStateOf(LiveConnection.enabled(application)); private set
    private var onScreen = true

    fun toggleInstantNotifications() {
        instantNotifications = !instantNotifications
        LiveConnection.setEnabled(getApplication(), instantNotifications)
        setOnScreen(true)
    }

    /** While the app is on screen, follows Arty's announcements live; in the background the 15-minute check does it. */
    fun setOnScreen(onScreen: Boolean) {
        this.onScreen = onScreen
        if (onScreen) Announcements.clearReply(getApplication())
        following?.cancel()
        // With instant notifications on, the live connection already follows the feed.
        following = if (!onScreen || instantNotifications) null else viewModelScope.launch(Dispatchers.IO) {
            while (true) {
                try {
                    Announcements.check(getApplication(), client, ANNOUNCEMENT_WAIT_SECONDS)
                } catch (error: ArtyException) {
                    delay(ANNOUNCEMENT_RETRY_MS)
                }
            }
        }
    }
    val canSend: Boolean get() = thinkingSince == null && pendingFiles.none { it.uploading } &&
        (draft.isNotBlank() || pendingFiles.any { it.attachment != null })

    var tasks by mutableStateOf<List<ArtyTask>>(emptyList()); private set
    var schedules by mutableStateOf<List<Schedule>>(emptyList()); private set
    var memory by mutableStateOf<List<MemoryEntity>>(emptyList()); private set
    var calendar by mutableStateOf<List<CalendarEvent>>(emptyList()); private set
    var lectures by mutableStateOf<List<Lecture>>(emptyList()); private set
    /** The lecture being read (with its notes and transcript), or null for the list. */
    var openedLecture by mutableStateOf<Lecture?>(null); private set
    var sendingLecture by mutableStateOf(false); private set
    var loading by mutableStateOf(false); private set

    /** A hands-free voice call is open (see CallScreen). */
    var inCall by mutableStateOf(false); private set
    /** Read-aloud is playing; a call listens again once it stops. */
    var speaking by mutableStateOf(false); private set

    private var waitJob: Job? = null
    private var speakNextReply = false
    private var ttsReady = false
    private var utterances = 0
    @Volatile private var lastUtterance = ""
    private lateinit var tts: TextToSpeech

    init {
        tts = TextToSpeech(application) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) {
                tts.language = Locale.UK
                tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) = Unit
                    override fun onDone(utteranceId: String?) = finishedSpeaking(utteranceId)
                    override fun onStop(utteranceId: String?, interrupted: Boolean) = finishedSpeaking(utteranceId)
                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String?) = finishedSpeaking(utteranceId)
                })
            }
        }
        conversations = store.list()
        val last = prefs.getString(PREF_LAST_CHAT, null)?.let(store::load) ?: conversations.firstOrNull()
        if (last != null) open(last)
        refreshModels()
    }

    // ---- chat ----

    fun send(text: String, spoken: Boolean = false) {
        val message = text.trim()
        if (thinkingSince != null || pendingFiles.any { it.uploading }) return
        val attachments = pendingFiles.mapNotNull { it.attachment }
        if (message.isEmpty() && attachments.isEmpty()) return
        speakNextReply = spoken || autoSpeak
        draft = ""
        pendingFiles.clear()
        val title = if (current.messages.isEmpty()) message.ifEmpty { attachments.first().name }.take(Conversation.TITLE_LENGTH) else current.title
        update(current.copy(title = title, messages = current.messages + StoredMessage("user", message, System.currentTimeMillis(), attachments = attachments)))
        startTurn()
    }

    // ---- files ----

    /** Uploads picked files straight away, so sending is instant. */
    fun attach(uris: List<Uri>) {
        val resolver = getApplication<Application>().contentResolver
        for (uri in uris) {
            val key = UUID.randomUUID().toString()
            var name = uri.lastPathSegment ?: "file"
            var size = -1L
            val mime = try {
                resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) {
                        cursor.getString(0)?.let { name = it }
                        if (!cursor.isNull(1)) size = cursor.getLong(1)
                    }
                }
                resolver.getType(uri) ?: "application/octet-stream"
            } catch (error: SecurityException) {
                pendingFiles += PendingFile(key, name, error = "Arty wasn't allowed to read it")
                notice = "$name: Arty wasn't allowed to read that file. Try sharing or attaching it again."
                continue
            }
            pendingFiles += PendingFile(key, name)
            viewModelScope.launch {
                val result = try {
                    PendingFile(key, name, attachment = withContext(Dispatchers.IO) { upload(uri, name, mime, size) })
                } catch (error: ArtyException) {
                    PendingFile(key, name, error = error.message ?: "Upload failed")
                } catch (error: java.io.IOException) {
                    PendingFile(key, name, error = "Couldn't read that file")
                } catch (error: SecurityException) {
                    PendingFile(key, name, error = "Arty wasn't allowed to read it")
                }
                val index = pendingFiles.indexOfFirst { it.key == key }
                if (index >= 0) pendingFiles[index] = result
                result.error?.let { notice = "$name: $it" }
            }
        }
    }

    private fun upload(uri: Uri, name: String, mime: String, knownSize: Long): Attachment {
        val resolver = getApplication<Application>().contentResolver
        if (knownSize >= 0) {
            return resolver.openInputStream(uri)?.use { client.upload(name, mime, knownSize, it) } ?: throw java.io.IOException("unreadable")
        }
        // Some apps don't say how big a file is, and the upload needs an exact length: copy it first.
        val copy = File.createTempFile("upload", null, getApplication<Application>().cacheDir)
        try {
            resolver.openInputStream(uri)?.use { input -> FileOutputStream(copy).use { input.copyTo(it) } } ?: throw java.io.IOException("unreadable")
            return copy.inputStream().use { client.upload(name, mime, copy.length(), it) }
        } finally {
            copy.delete()
        }
    }

    fun removePending(file: PendingFile) {
        pendingFiles.removeAll { it.key == file.key }
    }

    private val mediaLock = Mutex()

    val media = object : ArtyMedia {
        override suspend fun file(link: String): File = mediaLock.withLock { withContext(Dispatchers.IO) { cachedMedia(link) } }
        override fun save(link: String) { download(link) }
    }

    private fun cachedMedia(link: String): File {
        val folder = File(getApplication<Application>().cacheDir, MEDIA_FOLDER).apply { mkdirs() }
        val key = link.substringAfter(ArtyClient.DOWNLOAD_PATH, link).substringBefore('?')
        val extension = key.substringAfterLast('.', "").lowercase().filter { it.isLetterOrDigit() }.take(5).ifEmpty { "bin" }
        val digest = MessageDigest.getInstance("SHA-256").digest(key.toByteArray()).joinToString("") { "%02x".format(it) }
        val file = File(folder, "${digest.take(32)}.$extension")
        if (file.length() > 0) return file.also { it.setLastModified(System.currentTimeMillis()) }
        val partial = File(folder, "${file.name}.part")
        try {
            FileOutputStream(partial).use { client.download(link, it) }
            if (!partial.renameTo(file)) throw java.io.IOException("couldn't store ${file.name}")
        } finally {
            partial.delete()
        }
        trimMediaCache(folder)
        return file
    }

    private fun trimMediaCache(folder: File) {
        var total = 0L
        for (file in folder.listFiles().orEmpty().sortedByDescending { it.lastModified() }) {
            total += file.length()
            if (total > MEDIA_CACHE_BYTES) file.delete()
        }
    }

    /** Saves a file Arty made (a link in its reply) to Downloads, then offers to open it. */
    fun download(link: String) = viewModelScope.launch {
        val name = URLDecoder.decode(link.substringAfterLast('/').substringBefore('?'), "UTF-8").ifBlank { "arty-file" }
        notice = "Downloading $name…"
        try {
            val saved = withContext(Dispatchers.IO) { saveToDownloads(link, name) }
            noticeFile = saved
            notice = "Saved to Downloads: $name"
        } catch (error: ArtyException) {
            notice = error.message
        } catch (error: java.io.IOException) {
            notice = "Couldn't save $name: ${error.message}"
        }
    }

    private fun saveToDownloads(link: String, name: String): Uri? {
        val app = getApplication<Application>()
        val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase()) ?: "application/octet-stream"
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // Android 9 has no Downloads collection; keep it in the app's own folder there.
            val folder = app.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: throw java.io.IOException("no storage")
            FileOutputStream(File(folder, name)).use { client.download(link, it) }
            return null
        }
        val resolver = app.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: throw java.io.IOException("Downloads is not available")
        try {
            resolver.openOutputStream(uri)?.use { client.download(link, it) } ?: throw java.io.IOException("Downloads is not writable")
        } catch (error: Exception) {
            resolver.delete(uri, null, null)
            throw error
        }
        resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        return uri
    }

    /** Sends the conversation again after a failed reply. */
    fun retry() {
        if (thinkingSince != null) return
        update(current.copy(messages = current.messages.filterNot { it.failed }))
        if (current.messages.lastOrNull()?.role == "user") startTurn()
    }

    private fun deviceNote(): String {
        val battery = getApplication<Application>().getSystemService(Context.BATTERY_SERVICE) as? BatteryManager ?: return ""
        val percent = battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it != Int.MIN_VALUE }
        return batteryNote(percent, battery.isCharging)
    }

    private fun startTurn() {
        val recent = recentWindow(current.messages.filterNot { it.failed }, HISTORY_MESSAGES, HISTORY_STEP) { it.role == "user" }
            .map { ChatMessage(it.role, it.content, it.attachments) }
        val history = withDeviceNote(if (inCall) listOf(ChatMessage("system", CALL_INSTRUCTIONS)) + recent else recent, deviceNote())
        thinkingSince = System.currentTimeMillis()
        waitJob = viewModelScope.launch {
            try {
                val jobId = withContext(Dispatchers.IO) { client.startChat(history, current.model) }
                update(current.copy(pendingJobId = jobId))
                awaitReply(current.id, jobId)
            } catch (error: ArtyException) {
                finishTurn(current.id, StoredMessage("assistant", error.message ?: "Something went wrong.", System.currentTimeMillis(), failed = true))
            }
        }
    }

    private suspend fun awaitReply(conversationId: String, jobId: String) {
        val started = System.currentTimeMillis()
        while (System.currentTimeMillis() - started < GIVE_UP_MS) {
            val job = try {
                withContext(Dispatchers.IO) { client.chatJob(jobId) }
            } catch (error: ArtyException) {
                val failure = ChatWait.failure(error)
                if (failure != null) {
                    finishTurn(conversationId, StoredMessage("assistant", failure, System.currentTimeMillis(), failed = true))
                    return
                }
                null // offline for a moment: keep waiting
            }
            if (job != null && !job.finished) showPartial(conversationId, job.partial)
            if (job != null && job.finished) {
                val reply = if (job.status == "done") {
                    val (text, action) = PhoneAction.split(job.answer.orEmpty())
                    StoredMessage("assistant", text, System.currentTimeMillis(), job.toolsUsed, phoneAction = action?.toJson())
                } else {
                    StoredMessage("assistant", "Arty hit an error: ${job.error}", System.currentTimeMillis(), failed = true)
                }
                finishTurn(conversationId, reply)
                return
            }
            delay(POLL_MS)
        }
        finishTurn(conversationId, StoredMessage("assistant", "No answer after 20 minutes. Tap retry to ask again.", System.currentTimeMillis(), failed = true))
    }

    private fun finishTurn(conversationId: String, reply: StoredMessage) {
        val conversation = (if (current.id == conversationId) current else store.load(conversationId)) ?: return
        val updated = conversation.copy(messages = conversation.messages + reply, pendingJobId = null, updated = System.currentTimeMillis())
        if (!reply.failed) ArtyWidget.setLatest(getApplication(), reply.content)
        // Away from the app: "Arty has replied" with its first few words, like other assistants do.
        if (!onScreen) Announcements.showReply(getApplication(), conversationId, reply.content, reply.failed)
        // Just asked for: open the call or text straight away (it still waits for the user to press call or send).
        if (onScreen) PhoneAction.fromJson(reply.phoneAction)?.let { phoneRequest = it }
        if (current.id == conversationId) {
            update(updated)
            thinkingSince = null
            val readOutEarly = spokenEarly
            clearPartial()
            if (speakNextReply && (!reply.failed || inCall)) {
                // The answer starts with what was already read out, so only the rest is still to say.
                if (readOutEarly.isNotEmpty() && reply.content.startsWith(readOutEarly)) speakMore(reply.content.removePrefix(readOutEarly))
                else speak(reply.content)
            }
        } else {
            store.save(updated)
            conversations = store.list()
        }
    }

    private fun showPartial(conversationId: String, partial: String?) {
        if (partial.isNullOrBlank() || current.id != conversationId || partial == partialReply) return
        partialReply = partial
        if (speakNextReply && partial.startsWith(spokenEarly) && partial.length > spokenEarly.length) {
            speakMore(partial.removePrefix(spokenEarly))
            spokenEarly = partial
        }
    }

    private fun clearPartial() {
        partialReply = null
        spokenEarly = ""
    }

    private fun update(conversation: Conversation) {
        current = conversation.copy(updated = System.currentTimeMillis())
        store.save(current)
        conversations = store.list()
    }

    fun newChat() {
        waitJob?.cancel()
        thinkingSince = null
        clearPartial()
        current = Conversation(model = current.model)
        prefs.edit().remove(PREF_LAST_CHAT).apply()
    }

    /** Opens a conversation by id (tapping "Arty has replied"); does nothing if it no longer exists. */
    fun openById(conversationId: String) {
        if (current.id == conversationId) return showChat()
        store.load(conversationId)?.let { open(it) }
        showChat()
    }

    fun open(conversation: Conversation) {
        waitJob?.cancel()
        thinkingSince = null
        clearPartial()
        current = conversation
        prefs.edit().putString(PREF_LAST_CHAT, conversation.id).apply()
        conversation.pendingJobId?.let { jobId ->
            thinkingSince = System.currentTimeMillis()
            waitJob = viewModelScope.launch { awaitReply(conversation.id, jobId) }
        }
    }

    fun delete(conversation: Conversation) {
        store.delete(conversation.id)
        conversations = store.list()
        if (current.id == conversation.id) newChat()
    }

    fun chooseModel(model: String) {
        current = current.copy(model = model)
        if (current.messages.isNotEmpty()) store.save(current)
    }

    fun refreshModels() = viewModelScope.launch {
        try {
            models = withContext(Dispatchers.IO) { client.models() }
        } catch (error: ArtyException) {
            notice = error.message
        }
    }

    // ---- voice ----

    fun speak(text: String) = say(text, TextToSpeech.QUEUE_FLUSH)

    /** Reads [text] out after whatever is being said now. */
    private fun speakMore(text: String) {
        if (text.isNotBlank()) say(text, TextToSpeech.QUEUE_ADD)
    }

    private fun say(text: String, queueMode: Int) {
        if (!ttsReady) {
            notice = "Read-aloud isn't available: install or enable a text-to-speech engine in Android settings."
            return
        }
        val utterance = "arty-${++utterances}"
        lastUtterance = utterance
        speaking = true
        if (tts.speak(speakableText(text), queueMode, null, utterance) != TextToSpeech.SUCCESS) speaking = false
    }

    fun stopSpeaking() {
        tts.stop()
        speaking = false
        if (thinkingSince != null) speakNextReply = false // interrupted: don't read out the rest when it arrives
    }

    // TTS reports on its own thread. Speech is queued, so it's over when the last queued part is.
    private fun finishedSpeaking(utterance: String?) {
        if (utterance == lastUtterance) viewModelScope.launch(Dispatchers.Main) { speaking = false }
    }

    // ---- voice calls ----

    fun startCall() {
        inCall = true
    }

    fun endCall() {
        inCall = false
        speakNextReply = autoSpeak // a reply still on its way shouldn't be read out after hanging up
        stopSpeaking()
    }

    fun toggleAutoSpeak() {
        autoSpeak = !autoSpeak
        prefs.edit().putBoolean(PREF_AUTO_SPEAK, autoSpeak).apply()
    }

    // ---- tasks, schedules, memory ----

    private fun load(block: suspend () -> Unit) = viewModelScope.launch {
        loading = true
        try {
            block()
        } catch (error: ArtyException) {
            notice = error.message
        } finally {
            loading = false
        }
    }

    fun refreshTasks() = load { tasks = withContext(Dispatchers.IO) { client.tasks() } }

    /** Everything Arty has sent (the Updates tab), newest first. */
    var updates by mutableStateOf<List<Announcement>>(emptyList()); private set
    var updatesRequests by mutableStateOf(0); private set
    private var lastSeenUpdate by mutableStateOf(prefs.getLong(PREF_LAST_SEEN_UPDATE, -1L))
    val unseenUpdates: Int get() = updates.count { it.id > lastSeenUpdate }

    init {
        // New announcements (from the live feed or the background check) go straight into the list.
        Announcements.onFresh = { fresh ->
            viewModelScope.launch(Dispatchers.Main) { updates = (fresh.reversed() + updates).distinctBy { it.id }.take(MAX_UPDATES) }
        }
        viewModelScope.launch {
            try {
                loadUpdates()
            } catch (error: ArtyException) {
                // The badge waits for the next refresh; the Updates tab says so if it still fails.
            }
        }
    }

    private suspend fun loadUpdates() {
        updates = withContext(Dispatchers.IO) { client.announcements(0).announcements }.reversed()
        if (lastSeenUpdate < 0) markUpdatesSeen() // first run: what came before isn't news
    }

    fun refreshUpdates() = load { loadUpdates() }

    fun showUpdates() {
        updatesRequests++
        refreshUpdates()
    }

    fun markUpdatesSeen() {
        val newest = updates.maxOfOrNull { it.id } ?: return
        if (newest <= lastSeenUpdate) return
        lastSeenUpdate = newest
        prefs.edit().putLong(PREF_LAST_SEEN_UPDATE, newest).apply()
    }

    /** Approve, Deny, Done or Cancel from an update about a paused task. */
    fun decideUpdate(update: Announcement, action: String) = load {
        val taskId = update.taskId ?: return@load
        notice = withContext(Dispatchers.IO) { client.decideTask(taskId, action) }
        tasks = withContext(Dispatchers.IO) { client.tasks() }
    }

    fun decide(task: ArtyTask, action: String) = load {
        notice = withContext(Dispatchers.IO) { client.decideTask(task.id, action) }
        tasks = withContext(Dispatchers.IO) { client.tasks() }
    }

    fun createTask(instructions: String) = load {
        val task = withContext(Dispatchers.IO) { client.createTask(instructions) }
        notice = "Started task #${task.id}. It'll show up in Updates when it's done."
        tasks = withContext(Dispatchers.IO) { client.tasks() }
    }

    fun refreshSchedules() = load { schedules = withContext(Dispatchers.IO) { client.schedules() } }

    fun createSchedule(instructions: String, whenText: String) = load {
        notice = withContext(Dispatchers.IO) { client.createSchedule(instructions, whenText) }
        schedules = withContext(Dispatchers.IO) { client.schedules() }
    }

    fun cancelSchedule(schedule: Schedule) = load {
        withContext(Dispatchers.IO) { client.cancelSchedule(schedule.id) }
        schedules = withContext(Dispatchers.IO) { client.schedules() }
    }

    // ---- lectures ----

    fun refreshLectures() = load {
        LectureRecorder.saved = LectureRecorder.recordings(getApplication())
        lectures = withContext(Dispatchers.IO) { client.lectures() }
    }

    fun openLecture(lecture: Lecture) = load { openedLecture = withContext(Dispatchers.IO) { client.lecture(lecture.id) } }

    fun closeLecture() {
        openedLecture = null
    }

    /** "Quiz me" on a finished lecture: its revision cards are filed under its title. */
    fun quizOnLecture(lecture: Lecture) {
        openedLecture = null
        showChat()
        send("Quiz me on ${lecture.title.lowercase()}")
    }

    /** Sends a recording made in the app; it's deleted from the phone once Arty has it. */
    fun sendRecording(file: File, title: String) = sendLecture(title, afterwards = { file.delete() }) {
        file.inputStream().use { client.upload(file.name, "audio/mp4", file.length(), it) }
    }

    fun deleteRecording(file: File) {
        file.delete()
        LectureRecorder.saved = LectureRecorder.recordings(getApplication())
    }

    /** Sends an audio or video file from another app (a voice recorder, say). */
    fun sendLectureFile(uri: Uri, title: String) {
        val resolver = getApplication<Application>().contentResolver
        var name = uri.lastPathSegment ?: "recording"
        var size = -1L
        val mime = try {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    cursor.getString(0)?.let { name = it }
                    if (!cursor.isNull(1)) size = cursor.getLong(1)
                }
            }
            resolver.getType(uri) ?: "application/octet-stream"
        } catch (error: SecurityException) {
            notice = "Arty wasn't allowed to read that file."
            return
        }
        if (!mime.startsWith("audio/") && !mime.startsWith("video/")) {
            notice = "That isn't a recording: pick an audio or video file."
            return
        }
        sendLecture(title) { upload(uri, name, mime, size) }
    }

    private fun sendLecture(title: String, afterwards: () -> Unit = {}, upload: () -> Attachment) = viewModelScope.launch {
        sendingLecture = true
        try {
            val lecture = withContext(Dispatchers.IO) { client.startLecture(upload().fileId, title) }
            afterwards()
            lectures = listOf(lecture) + lectures
            notice = "Sent. Arty will tell you when the notes are ready (it takes about half as long as the recording)."
        } catch (error: ArtyException) {
            notice = error.message
        } catch (error: java.io.IOException) {
            notice = "Couldn't read the recording."
        } finally {
            sendingLecture = false
            LectureRecorder.saved = LectureRecorder.recordings(getApplication())
        }
    }

    fun refreshCalendar() = load { calendar = withContext(Dispatchers.IO) { client.calendar(CALENDAR_DAYS) } }

    fun addEvent(title: String, whenText: String) = load {
        notice = withContext(Dispatchers.IO) { client.addEvent(title, whenText) }
        calendar = withContext(Dispatchers.IO) { client.calendar(CALENDAR_DAYS) }
    }

    fun deleteEvent(event: CalendarEvent) = load {
        val id = event.id ?: return@load
        withContext(Dispatchers.IO) { client.deleteEvent(id) }
        notice = "Removed ${event.title}."
        calendar = withContext(Dispatchers.IO) { client.calendar(CALENDAR_DAYS) }
    }

    fun refreshMemory() = load { memory = withContext(Dispatchers.IO) { client.memory() } }

    override fun onCleared() {
        Announcements.onFresh = null
        tts.shutdown()
    }
}
