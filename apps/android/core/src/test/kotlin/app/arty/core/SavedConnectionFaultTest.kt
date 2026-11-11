package app.arty.core

import java.security.InvalidKeyException
import java.security.KeyStoreException
import java.security.ProviderException
import java.security.UnrecoverableKeyException
import javax.crypto.AEADBadTagException
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class SavedConnectionFaultTest {
    @Test
    fun `damaged data, a wrong tag and a key that is gone for good clear the save`() {
        assertTrue(StoredConnection.cannotEverBeRead(AEADBadTagException("tag mismatch")))
        assertTrue(StoredConnection.cannotEverBeRead(IllegalArgumentException("bad base-64")))
        assertTrue(StoredConnection.cannotEverBeRead(InvalidKeyException("key permanently invalidated")))
    }

    @Test
    fun `a busy or restarting Keystore keeps the save for the next start`() {
        assertFalse(StoredConnection.cannotEverBeRead(ProviderException("keystore busy")))
        assertFalse(StoredConnection.cannotEverBeRead(KeyStoreException("system error")))
        assertFalse(StoredConnection.cannotEverBeRead(UnrecoverableKeyException("try again")))
    }
}
