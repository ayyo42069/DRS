"""
DRS Auto Race Bot - CLI Entry Point

Usage:
    python -m src.main
    python src/main.py
"""
import asyncio
import sys
import traceback
from pathlib import Path

# Add parent to path for imports when running directly
if __name__ == "__main__":
    parent = Path(__file__).parent.parent
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))


def main() -> int:
    """Main entry point."""
    # Import here to ensure path is set up first
    from src import __version__
    from src.config import Config, ConfigError
    
    print()
    print("╔════════════════════════════════════════════════════╗")
    print(f"║           DRS Auto Race Bot v{__version__:<10}          ║")
    print("╠════════════════════════════════════════════════════╣")
    print("║            Made by https://kristof.best            ║")
    print("╚════════════════════════════════════════════════════╝")
    print()
    
    # Load config
    try:
        config = Config.load()
        print("[OK] Config loaded from config.toml")
        print(f"     Window: {config.window_title}")
        print(f"     Min fuel: {config.min_fuel}")
        print(f"     Threshold: {config.match_threshold}")
        print(f"     Scales: {config.scales}")
        print()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("[ERROR] Please ensure config.toml exists in the project root")
        return 1
    except ConfigError as e:
        print(f"[ERROR] Configuration error: {e}")
        return 1
    except (OSError, IOError) as e:
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
    except (RuntimeError, ValueError, OSError) as e:
        print(f"\n[ERROR] Fatal error: {e}")
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

