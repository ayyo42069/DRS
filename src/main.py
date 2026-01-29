"""
DRS Auto Race Bot - CLI Entry Point

Usage:
    python -m src_cli.main
    python src_cli/main.py
"""
import asyncio
import sys
from pathlib import Path

# Add parent to path for imports
if __name__ == "__main__":
    parent = Path(__file__).parent.parent
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))


def main():
    """Main entry point."""
    print()
    print("╔════════════════════════════════════════════════════╗")
    print("║                DRS Auto Race Bot v4.0              ║")
    print("╠════════════════════════════════════════════════════╣")
    print("║            Made by https://kristof.best            ║")
    print("╚════════════════════════════════════════════════════╝")
    print()
    
    # Load config
    try:
        from src.config import Config
        config = Config.load()
        print(f"[OK] Config loaded from config.toml")
        print(f"     Window: {config.window_title}")
        print(f"     Min fuel: {config.min_fuel}")
        print(f"     Threshold: {config.match_threshold}")
        print(f"     Scales: {config.scales}")
        print()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("[ERROR] Please ensure config.toml exists in the project root")
        return 1
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        return 1
    
    # Create and run bot
    try:
        from src.bot import DRSBot
        bot = DRSBot(config)
        
        # Run the async main loop
        asyncio.run(bot.run())
        
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
