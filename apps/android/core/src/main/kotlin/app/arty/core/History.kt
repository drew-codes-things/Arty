package app.arty.core

/**
 * The recent part of a conversation to send with each message: the last [size] to [size] + [step] - 1
 * messages, starting with one of the user's. Its start only moves every [step] messages, so the brain's
 * cached prompt stays valid; a start that moved with every message would make the model re-read the
 * whole conversation each time.
 */
fun <T> recentWindow(messages: List<T>, size: Int, step: Int, isUser: (T) -> Boolean): List<T> {
    require(size > 0 && step > 0) { "size and step must be positive" }
    if (messages.size <= size) return messages
    var start = (messages.size - size) / step * step
    while (start < messages.size && !isUser(messages[start])) start++
    return messages.subList(start, messages.size)
}
