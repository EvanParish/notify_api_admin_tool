"""Debug script to see what's happening during API key sync."""
import asyncio
import sys
from app.config import load_config
from app.sync import SyncManager
from app.api_client import HttpNotificationAPI
from app.db import init_engine
from app.crypto import EncryptionManager
from app.repository import get_setting, get_secure_setting

async def main():
    config = load_config()
    encryption = EncryptionManager(config.master_key)
    init_engine(config.database_path)
    
    # Use development environment
    env = "development"
    
    # Try to get settings
    base_url = await get_setting(f"base_url_{env}")
    print(f"Looking for: base_url_{env}")
    print(f"Found: {base_url}")
    
    if not base_url:
        print(f"\nTrying alternate key: base_url_dev")
        base_url = await get_setting("base_url_dev")
        print(f"Found: {base_url}")
    
    if not base_url:
        base_url = config.api_hosts.get(env)
        print(f"\nFalling back to config default: {base_url}")
    
    if not base_url:
        print("\n❌ No base URL found! This is the problem.")
        return
    
    basic_user = await get_secure_setting(f"basic_username_{env}", encryption)
    basic_pass = await get_secure_setting(f"basic_password_{env}", encryption)
    
    if not basic_user:
        print(f"\nTrying alternate key: basic_username_dev")
        basic_user = await get_secure_setting("basic_username_dev", encryption)
        basic_pass = await get_secure_setting("basic_password_dev", encryption)
    
    print(f"\nFinal configuration:")
    print(f"  Base URL: {base_url}")
    print(f"  Auth configured: {bool(basic_user and basic_pass)}")
    
    # Build API client
    api = HttpNotificationAPI(
        base_url=base_url,
        basic_username=basic_user,
        basic_password=basic_pass
    )
    
    # Try to sync API keys
    print(f"\nTesting API key sync...")
    manager = SyncManager(api, max_concurrency=5)
    
    messages = []
    async def progress(msg):
        messages.append(msg)
        print(f"  Progress: {msg}")
    
    try:
        await manager.sync_api_keys(progress=progress)
        print(f"\n✓ Sync completed!")
        print(f"Total progress messages: {len(messages)}")
        
        # Count how many had "No API keys"
        no_keys = sum(1 for m in messages if "No API keys" in m)
        has_keys = sum(1 for m in messages if "API keys for" in m and "No API keys" not in m)
        
        print(f"  Services with API keys: {has_keys}")
        print(f"  Services without API keys (404): {no_keys}")
        
    except Exception as e:
        print(f"\n✗ Sync failed: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
