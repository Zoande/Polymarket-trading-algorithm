"""
Cloud Sync Setup Helper
========================
This script helps you set up cloud synchronization with your friend.
"""

import sys
import subprocess

def check_supabase_installed():
    """Check if supabase package is installed."""
    try:
        import supabase
        return True
    except ImportError:
        return False

def install_supabase():
    """Install the supabase package."""
    print("Installing supabase package...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "supabase"])
    print("✅ Supabase installed successfully!")

def test_connection(url: str, key: str):
    """Test the Supabase connection."""
    from supabase import create_client
    
    try:
        client = create_client(url, key)
        # Try to query the bot_state table
        result = client.table('bot_state').select('instance_id').limit(1).execute()
        return True, "Connection successful!"
    except Exception as e:
        return False, str(e)

def update_config(url: str, key: str, instance_id: str):
    """Update the config.yaml with cloud sync settings."""
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent / "config.yaml"
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    config['cloud_sync'] = {
        'enabled': True,
        'supabase_url': url,
        'supabase_key': key,
        'bot_instance_id': instance_id,
    }
    
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print("✅ config.yaml updated!")

def main():
    print("=" * 60)
    print("  POLYMARKET BOT - CLOUD SYNC SETUP")
    print("=" * 60)
    print()
    
    # Step 1: Check/install supabase
    if not check_supabase_installed():
        print("⚠️  The 'supabase' package is not installed.")
        response = input("Would you like to install it now? (y/n): ").strip().lower()
        if response == 'y':
            install_supabase()
        else:
            print("❌ Cannot proceed without supabase package.")
            return
    else:
        print("✅ Supabase package is installed")
    
    print()
    print("=" * 60)
    print("  SUPABASE SETUP INSTRUCTIONS")
    print("=" * 60)
    print("""
1. Go to https://supabase.com and create a FREE account
2. Click "New Project" and create a project (any name)
3. Wait for the project to be created (~2 minutes)
4. Go to Settings > API in your project dashboard
5. You'll need:
   - Project URL (looks like: https://xxxxx.supabase.co)
   - anon public key (a long string starting with 'eyJ...')
   
6. Go to SQL Editor in Supabase
7. Create a "New query"
8. Open the file 'create_tables.sql' in this folder
9. Copy ALL the SQL and paste it into Supabase
10. Click "Run" to create the tables
""")
    
    print("=" * 60)
    print()
    
    # Get credentials
    url = input("Enter your Supabase Project URL: ").strip()
    if not url.startswith('https://') or 'supabase.co' not in url:
        print("⚠️  That doesn't look like a valid Supabase URL")
        print("   It should look like: https://xxxxx.supabase.co")
        return
    
    key = input("Enter your Supabase anon key: ").strip()
    if not key.startswith('eyJ'):
        print("⚠️  That doesn't look like a valid Supabase anon key")
        print("   It should start with 'eyJ...'")
        return
    
    instance_id = input("Enter a shared bot instance ID (default: shared_bot): ").strip()
    if not instance_id:
        instance_id = "shared_bot"
    
    print()
    print("Testing connection...")
    success, message = test_connection(url, key)
    
    if success:
        print(f"✅ {message}")
        print()
        
        # Update config
        response = input("Would you like to save these settings to config.yaml? (y/n): ").strip().lower()
        if response == 'y':
            update_config(url, key, instance_id)
            
            print()
            print("=" * 60)
            print("  SETUP COMPLETE!")
            print("=" * 60)
            print(f"""
Your cloud sync is now configured!

Instance ID: {instance_id}

TO SHARE WITH YOUR FRIEND:
1. Send them these values:
   - Supabase URL: {url}
   - Supabase Key: {key}  
   - Instance ID: {instance_id}
   
2. They should put these in their config.yaml under 'cloud_sync'

3. Make sure you both use the SAME instance_id!

Now when either of you runs the bot, your data will sync automatically.
""")
    else:
        print(f"❌ Connection failed: {message}")
        print()
        print("Common issues:")
        print("  - Did you run the create_tables.sql in Supabase SQL Editor?")
        print("  - Is your URL and key correct?")
        print("  - Try creating a new project if this one doesn't work")

if __name__ == "__main__":
    main()
