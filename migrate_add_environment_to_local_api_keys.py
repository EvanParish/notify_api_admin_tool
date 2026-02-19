#!/usr/bin/env python3
"""
Migration script to add environment column to local_api_keys table.

This script adds the environment column required for environment-specific local API keys.
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

from app.config import load_config


async def migrate():
    """Add environment column to local_api_keys table."""
    config = load_config()
    db_path = Path(config.database_path)

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        print(
            "No migration needed - database will be created with correct schema on first run"
        )
        return

    print(f"Migrating database at {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(local_api_keys)")
        columns = [row[1] for row in cursor.fetchall()]

        if "environment" in columns:
            print("✓ Column 'environment' already exists in local_api_keys table")
            print("No migration needed")
            return

        print("Adding environment column to local_api_keys table...")

        cursor.execute(
            """
            ALTER TABLE local_api_keys
            ADD COLUMN environment TEXT
            """
        )

        conn.commit()
        print("✓ Successfully added environment column")

        cursor.execute("PRAGMA table_info(local_api_keys)")
        columns = [row[1] for row in cursor.fetchall()]

        if "environment" in columns:
            print("✓ Migration verified - column exists")
        else:
            print("✗ Migration verification failed")
            sys.exit(1)

    except Exception as exc:
        print(f"✗ Migration failed: {exc}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

    print("\n✓ Migration complete!")
    print("You can now start the application.")


if __name__ == "__main__":
    asyncio.run(migrate())
