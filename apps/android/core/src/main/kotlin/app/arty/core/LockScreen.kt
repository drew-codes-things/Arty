package app.arty.core

object LockScreen {
    private const val AUTHENTICATED_ACTIONS_SDK = 31
    private const val APPROVAL = "approval"
    const val HIDDEN_TITLE = "Arty needs your answer"
    const val HIDDEN_TEXT = "Unlock your phone to see what Arty is asking."

    fun allowsActions(sdk: Int): Boolean = sdk >= AUTHENTICATED_ACTIONS_SDK

    fun hidesText(item: Announcement): Boolean = item.kind == APPROVAL || item.taskId != null
}
