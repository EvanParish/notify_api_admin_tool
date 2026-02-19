#!/usr/bin/env python3
"""
Fix environment settings to match config keys.

This script copies settings from old keys (dev, prod) to new keys (development, production).
"""

import asyncio
import sqlite3
from pathlib import Path

from app.config import load_config
from app.db import init_engine
from app.repository import (
    get_setting,
    set_setting,
    get_secure_setting,
    set_secure_setting,
)
from app.crypto import EncryptionManager


async def migrate_settings():
    """Copy settings from old keys to new keys."""
    config = load_config()
    encryption = EncryptionManager(config.master_key)
    init_engine(config.database_path)

    # Mapping of old keys to new keys
    mappings = {
        "dev": "development",
        "prod": "production",
    }

    print("Copying environment settings to match config keys...\n")

    for old_key, new_key in mappings.items():
        print(f"Copying {old_key} → {new_key}")

        # Copy base_url
        old_url = await get_setting(f"base_url_{old_key}")
        if old_url:
            await set_setting(f"base_url_{new_key}", old_url)
            print(f"  ✓ base_url: {old_url}")

        # Copy basic auth username
        old_user = await get_secure_setting(f"basic_username_{old_key}", encryption)
        if old_user:
            await set_secure_setting(f"basic_username_{new_key}", old_user, encryption)
            print(f"  ✓ basic_username: [configured]")

        # Copy basic auth password
        old_pass = await get_secure_setting(f"basic_password_{old_key}", encryption)
        if old_pass:
            await set_secure_setting(f"basic_password_{new_key}", old_pass, encryption)
            print(f"  ✓ basic_password: [configured]")

        print()

    print("✓ Settings migration complete!\n")
    print("Environment keys now available:")
    print("  - development (copied from dev)")
    print("  - production (copied from prod)")
    print("  - perf (already exists)")
    print("  - staging (already exists)")


if __name__ == "__main__":
    asyncio.run(migrate_settings())
