import pytest
from cryptography.fernet import InvalidToken
from app.crypto import EncryptionManager
from app.db import init_engine, create_all


@pytest.mark.asyncio
async def test_encryption_manager_encrypt_decrypt(initialized_db):
    manager = EncryptionManager("test-master-key-123")

    plaintext = "secret-api-key-12345"
    encrypted = await manager.encrypt(plaintext)

    assert encrypted != plaintext
    assert isinstance(encrypted, str)

    decrypted = await manager.decrypt(encrypted)
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_encryption_manager_multiple_encryptions_differ(initialized_db):
    manager = EncryptionManager("test-master-key-123")

    plaintext = "same-secret"
    encrypted1 = await manager.encrypt(plaintext)
    encrypted2 = await manager.encrypt(plaintext)

    # Fernet uses random IV, so encryptions differ
    assert encrypted1 != encrypted2

    # But both decrypt to the same value
    assert await manager.decrypt(encrypted1) == plaintext
    assert await manager.decrypt(encrypted2) == plaintext


@pytest.mark.asyncio
async def test_encryption_manager_wrong_master_key(initialized_db):
    manager1 = EncryptionManager("master-key-1")
    manager2 = EncryptionManager("master-key-2")

    plaintext = "secret"
    encrypted = await manager1.encrypt(plaintext)

    # Different master key should fail to decrypt
    with pytest.raises(ValueError, match="Failed to decrypt"):
        await manager2.decrypt(encrypted)


@pytest.mark.asyncio
async def test_encryption_manager_invalid_token(initialized_db):
    manager = EncryptionManager("test-master-key")

    with pytest.raises(ValueError, match="Failed to decrypt"):
        await manager.decrypt("not-a-valid-token")


@pytest.mark.asyncio
async def test_encryption_manager_empty_string(initialized_db):
    manager = EncryptionManager("test-master-key")

    encrypted = await manager.encrypt("")
    decrypted = await manager.decrypt(encrypted)
    assert decrypted == ""


@pytest.mark.asyncio
async def test_encryption_manager_unicode(initialized_db):
    manager = EncryptionManager("test-master-key")

    plaintext = "Hello 世界 🌍"
    encrypted = await manager.encrypt(plaintext)
    decrypted = await manager.decrypt(encrypted)
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_encryption_manager_reuses_fernet(initialized_db):
    manager = EncryptionManager("test-master-key")

    # First encryption builds the Fernet instance
    await manager.encrypt("test1")
    fernet1 = manager._fernet

    # Second encryption reuses it
    await manager.encrypt("test2")
    fernet2 = manager._fernet

    assert fernet1 is fernet2


@pytest.mark.asyncio
async def test_encryption_manager_salt_persistence(initialized_db):
    # First manager creates salt
    manager1 = EncryptionManager("master-key", iterations=100000)
    encrypted = await manager1.encrypt("test-value")

    # Second manager with same master key should decrypt successfully
    manager2 = EncryptionManager("master-key", iterations=100000)
    decrypted = await manager2.decrypt(encrypted)
    assert decrypted == "test-value"


@pytest.mark.asyncio
async def test_encryption_manager_custom_iterations(initialized_db):
    manager = EncryptionManager("test-key", iterations=50000)

    plaintext = "test-secret"
    encrypted = await manager.encrypt(plaintext)
    decrypted = await manager.decrypt(encrypted)
    assert decrypted == plaintext
