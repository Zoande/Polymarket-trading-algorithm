#!/usr/bin/env python3
"""
Polymarket Trading Algorithm - Main Entry Point
================================================

This is the main entry point for the Polymarket Trading Bot.
Run this file to start the application.

Usage:
    python main.py           # Start the GUI
    python main.py --headless  # Start in headless mode (no GUI)
"""

import sys
import os
from pathlib import Path

# Add the project root to Python path for proper imports
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# Set working directory to project root
os.chdir(PROJECT_ROOT)


def main():
    """Main entry point for the trading bot."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Algorithm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                  Start the GUI
    python main.py --headless       Start in headless mode (no UI)
    python main.py --test           Run API tests
        """
    )
    
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless mode without GUI"
    )
    
    parser.add_argument(
        "--test",
        action="store_true", 
        help="Run API connectivity tests"
    )
    
    args = parser.parse_args()
    
    if args.test:
        # Run tests
        print("Running API tests...")
        import subprocess
        test_script = PROJECT_ROOT / "scripts" / "test_api.py"
        subprocess.run([sys.executable, str(test_script)])
        return
    
    if args.headless:
        # Run headless mode
        print("Starting headless mode...")
        from scripts.headless_runner import main as run_headless
        run_headless()
        return
    
    # Start the GUI (default)
    print("Starting Polymarket Trading Bot...")
    
    # Import and run the GUI
    try:
        # Import the trading bot UI
        import trading_bot_v2
        
        # Create and run the application
        if hasattr(trading_bot_v2, 'main'):
            trading_bot_v2.main()
        elif hasattr(trading_bot_v2, 'TradingBotApp'):
            import tkinter as tk
            root = tk.Tk()
            app = trading_bot_v2.TradingBotApp(root)
            root.mainloop()
        else:
            print("Error: Could not find entry point in trading_bot_v2.py")
            sys.exit(1)
            
    except ImportError as e:
        print(f"Error importing modules: {e}")
        print("\nMake sure all dependencies are installed:")
        print("  pip install requests pyyaml")
        sys.exit(1)
    except Exception as e:
        print(f"Error starting application: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
