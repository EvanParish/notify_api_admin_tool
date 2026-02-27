import base64
import os
from typing import Optional, Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


@runtime_checkable
class SaltProvider(Protocol):
    """Async interface for retrieving/storing the encryption salt."""

    async def get_salt(self) -> Optional[bytes]: ...
    async def store_salt(self, salt: bytes) -> None: ...


class EncryptionManager:
    def __init__(
        self,
        master_key: str,
        iterations: int = 390000,
        salt_provider: Optional[SaltProvider] = None,
    ) -> None:
        self.master_key = master_key.encode()
        self.iterations = iterations
        self._salt_provider = salt_provider
        self._fernet: Optional[Fernet] = None

    async def _get_or_create_salt(self) -> bytes:
        if self._salt_provider is None:
            raise RuntimeError("No SaltProvider configured for EncryptionManager")
        existing = await self._salt_provider.get_salt()
        if existing is not None:
            return existing
        salt = os.urandom(16)
        await self._salt_provider.store_salt(salt)
        return salt

    async def _build_fernet(self) -> Fernet:
        if self._fernet:
            return self._fernet
        salt = await self._get_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.iterations,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.master_key))
        self._fernet = Fernet(key)
        return self._fernet

    async def encrypt(self, plaintext: str) -> str:
        fernet = await self._build_fernet()
        token = fernet.encrypt(plaintext.encode())
        return token.decode()

    async def decrypt(self, token: str) -> str:
        fernet = await self._build_fernet()
        try:
            plaintext = fernet.decrypt(token.encode())
        except InvalidToken:
            raise ValueError("Failed to decrypt: invalid token or master key")
        return plaintext.decode()
