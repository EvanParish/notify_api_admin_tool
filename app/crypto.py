import base64
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import select

from .db import get_session
from .models import Setting


class EncryptionManager:
    def __init__(self, master_key: str, iterations: int = 390000) -> None:
        self.master_key = master_key.encode()
        self.iterations = iterations
        self._fernet: Optional[Fernet] = None

    async def _get_or_create_salt(self) -> bytes:
        async with get_session() as session:
            result = await session.execute(select(Setting).where(Setting.key == "encryption_salt"))
            setting: Setting | None = result.scalar_one_or_none()
            if setting:
                return base64.urlsafe_b64decode(setting.value)

            salt = os.urandom(16)
            setting = Setting(key="encryption_salt", value=base64.urlsafe_b64encode(salt).decode())
            session.add(setting)
            await session.commit()
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
