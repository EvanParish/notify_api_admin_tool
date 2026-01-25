#!/usr/bin/env python3
"""
Migration script to add service_id column to api_keys table.

This script adds the service_id column that was added to the ApiKey model.
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

from app.config import load_config


async def migrate():
    """Add service_id column to api_keys table."""
    config = load_config()
    db_path = Path(config.database_path)
    
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        print("No migration needed - database will be created with correct schema on first run")
        return
    
    print(f"Migrating database at {db_path}")
    
    # Connect to SQLite database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if the column already exists
        cursor.execute("PRAGMA table_info(api_keys)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "service_id" in columns:
            print("✓ Column 'service_id' already exists in api_keys table")
            print("No migration needed")
            return
        
        print("Adding service_id column to api_keys table...")
        
        # Add the service_id column (nullable with foreign key)
        cursor.execute("""
            ALTER TABLE api_keys 
            ADD COLUMN service_id TEXT 
            REFERENCES services(id)
        """)
        
        conn.commit()
        print("✓ Successfully added service_id column")
        
        # Verify the column was added
        cursor.execute("PRAGMA table_info(api_keys)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "service_id" in columns:
            print("✓ Migration verified - column exists")
        else:
            print("✗ Migration verification failed")
            sys.exit(1)
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()
    
    print("\n✓ Migration complete!")
    print("You can now start the application.")


if __name__ == "__main__":
    asyncio.run(migrate())
