# DRS Auto Race Bot

Automated racing bot for Drag Racing: Streets mobile game.

## Requirements

- Python 3.10+
- ADB (Android Debug Bridge)
- Android emulator (MuMu, LDPlayer, BlueStacks)

## Quick Start

1. Copy `config.example.toml` to `config.toml`
2. Configure your ADB address in `config.toml`
3. Run `run_cli.bat` or `python -m src.main`

## Features

- Automated autorace farming
- Smart fuel management with wait/resume
- Daily scheduled game restart
- Full ADB control (no mouse stealing)
- Discord webhook notifications

## Configuration

Edit `config.toml` to customize:

| Setting | Description |
|---------|-------------|
| `adb.address` | Emulator ADB address (e.g. `127.0.0.1:7555`) |
| `bot.min_fuel` | Minimum fuel to start racing |
| `schedule.daily_restart_hour` | Hour for daily restart (UTC+1) |
| `discord.webhook_url` | Discord notifications |

## Author

Feel free to fork, modify and use it.

Made by [Kristof](https://kristof.best)
