package app.arty.core

import org.json.JSONException
import org.json.JSONObject

class SetupCodeException(message: String) : Exception(message)

data class SetupCode(val address: String, val apiKey: String, val accessClientId: String, val accessClientSecret: String) {
    companion object {
        const val FORMAT_VERSION = 1
        private const val NOT_A_SETUP_CODE =
            "That isn't an Arty setup code. On the computer, open Arty's tray menu and choose Phone setup code."

        fun parse(scanned: String): SetupCode {
            val json = readObject(scanned)
            val version = wholeNumber(json.opt("arty"))
            if (version < FORMAT_VERSION) throw SetupCodeException(NOT_A_SETUP_CODE)
            if (version > FORMAT_VERSION) throw SetupCodeException("This setup code is from a newer Arty. Update this app, then scan it again.")
            val code = SetupCode(field(json, "url"), field(json, "key"), field(json, "id"), field(json, "secret"))
            when {
                code.address.isEmpty() -> throw SetupCodeException("The setup code has no address in it.")
                hasUsername(code.address) -> throw SetupCodeException(
                    "The setup code's address has a username in it, which can hide where it really goes, so it was refused.",
                )
                code.apiKey.isEmpty() -> throw SetupCodeException("The setup code has no API key in it.")
                code.accessClientId.isEmpty() != code.accessClientSecret.isEmpty() ->
                    throw SetupCodeException("The setup code has only half of the Cloudflare Access token.")
            }
            return code
        }

        private fun readObject(scanned: String): JSONObject = try {
            JSONObject(scanned.trim())
        } catch (error: JSONException) {
            throw SetupCodeException(NOT_A_SETUP_CODE)
        }

        private fun hasUsername(address: String): Boolean =
            address.trim().substringAfter("://", "").substringBefore('/').substringBefore('?').substringBefore('#').contains('@')

        private fun wholeNumber(value: Any?): Int {
            val number = (value as? Number)?.toDouble() ?: return 0
            if (number.isNaN() || number != Math.floor(number) || number < 0) return 0
            return number.coerceAtMost(Int.MAX_VALUE.toDouble()).toInt()
        }

        private fun field(json: JSONObject, name: String): String = (json.opt(name) as? String)?.trim().orEmpty()
    }
}
