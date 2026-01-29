"""
Configuration management - loads from config.toml with validation.
All values MUST be defined in the config file.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def get_base_dir() -> Path:
    """Get the base directory (where EXE or script is located)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


class ConfigError(Exception):
    """Raised when configuration is invalid or missing required values."""
    pass


@dataclass
class Config:
    """Bot configuration - all values loaded from config.toml."""
    
    # Window
    window_title: str
    
    # Bot settings
    min_fuel: int
    fuel_per_race: int
    max_fuel: int
    fuel_regen_seconds: float
    
    # Timing
    transition_delay: float
    race_duration: float
    race_buffer_time: float
    fuel_check_interval: float
    
    # Matching
    match_threshold: float
    scales: List[float]
    target_width: int
    
    # Debug
    save_debug_images: bool
    debug_dir: Path
    
    # Paths
    templates_dir: Path
    
    # Discord (optional)
    discord_enabled: bool
    discord_webhook_url: str
    discord_send_race_start: bool
    discord_send_race_complete: bool
    discord_send_fuel_wait: bool
    discord_send_errors: bool
    
    # Crash recovery
    package_name: str
    boot_wait_seconds: float
    
    # ADB
    adb_enabled: bool
    adb_address: str
    
    # Schedule
    daily_restart_enabled: bool
    daily_restart_hour: int
    daily_restart_minute: int
    last_daily_restart: Optional[str]  # ISO format timestamp
    
    # Internal - path to config file for saving
    _config_path: Optional[Path] = field(default=None, repr=False)
    
    @classmethod
    def _get_required(cls, data: dict, section: str, key: str) -> any:
        """Get a required config value with helpful error message."""
        if section not in data:
            raise ConfigError(f"Missing required config section: [{section}]")
        if key not in data[section]:
            raise ConfigError(f"Missing required config value: [{section}].{key}")
        return data[section][key]
    
    @classmethod
    def _get_optional(cls, data: dict, section: str, key: str, default: any) -> any:
        """Get an optional config value with default."""
        return data.get(section, {}).get(key, default)
    
    @classmethod
    def _parse_scales(cls, scales_value) -> List[float]:
        """Parse scales from config (can be string or list)."""
        if isinstance(scales_value, str):
            return [float(s.strip()) for s in scales_value.split(",")]
        elif isinstance(scales_value, (list, tuple)):
            return [float(s) for s in scales_value]
        else:
            raise ConfigError(f"Invalid scales format: {type(scales_value)}")
    
    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> Config:
        """Load configuration from TOML file."""
        if config_path is None:
            config_path = get_base_dir() / "config.toml"
        
        if not config_path.exists():
            example_path = config_path.parent / "config.example.toml"
            if example_path.exists():
                raise ConfigError(
                    f"Config file not found: {config_path}\n"
                    f"Copy {example_path} to {config_path} and fill in your values."
                )
            raise ConfigError(f"Config file not found: {config_path}")
        
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Invalid TOML syntax in config file: {e}")
        
        base_dir = get_base_dir()
        
        # Parse required values with validation
        try:
            scales = cls._parse_scales(
                cls._get_optional(data, "matching", "scales", "0.7,0.8,0.9,1.0,1.1,1.2,1.3")
            )
        except ValueError as e:
            raise ConfigError(f"Invalid scales value: {e}")
        
        return cls(
            # Window (required)
            window_title=cls._get_required(data, "window", "title"),
            
            # Bot (required)
            min_fuel=cls._get_required(data, "bot", "min_fuel"),
            fuel_per_race=cls._get_required(data, "bot", "fuel_per_race"),
            max_fuel=cls._get_required(data, "bot", "max_fuel"),
            fuel_regen_seconds=cls._get_required(data, "bot", "fuel_regen_seconds"),
            
            # Timing
            transition_delay=cls._get_required(data, "timing", "transition_delay"),
            race_duration=cls._get_required(data, "timing", "race_duration"),
            race_buffer_time=cls._get_required(data, "timing", "race_buffer_time"),
            fuel_check_interval=cls._get_required(data, "timing", "fuel_check_interval"),
            
            # Matching
            match_threshold=cls._get_required(data, "matching", "threshold"),
            scales=scales,
            target_width=cls._get_optional(data, "matching", "target_width", 0),
            
            # Debug
            save_debug_images=cls._get_optional(data, "debug", "save_images", False),
            debug_dir=base_dir / "debug",
            
            # Paths
            templates_dir=base_dir / "templates",
            
            # Discord (optional)
            discord_enabled=cls._get_optional(data, "discord", "enabled", False),
            discord_webhook_url=cls._get_optional(data, "discord", "webhook_url", ""),
            discord_send_race_start=cls._get_optional(data, "discord", "send_race_start", False),
            discord_send_race_complete=cls._get_optional(data, "discord", "send_race_complete", False),
            discord_send_fuel_wait=cls._get_optional(data, "discord", "send_fuel_wait", False),
            discord_send_errors=cls._get_optional(data, "discord", "send_errors", False),
            
            # Crash recovery (ADB)
            package_name=cls._get_optional(data, "adb", "package_name", "mobi.square.sr.android"),
            boot_wait_seconds=cls._get_optional(data, "adb", "boot_wait_seconds", 150),
            
            # ADB
            adb_enabled=cls._get_optional(data, "adb", "enabled", False),
            adb_address=cls._get_optional(data, "adb", "address", "127.0.0.1:7555"),
            
            # Schedule
            daily_restart_enabled=cls._get_optional(data, "schedule", "daily_restart_enabled", False),
            daily_restart_hour=cls._get_optional(data, "schedule", "daily_restart_hour", 6),
            daily_restart_minute=cls._get_optional(data, "schedule", "daily_restart_minute", 0),
            last_daily_restart=cls._get_optional(data, "schedule", "last_daily_restart", None),
            _config_path=config_path,
        )
    
    def save_last_restart(self, timestamp: datetime) -> None:
        """Save the last daily restart timestamp to config.toml."""
        if self._config_path is None:
            print("[WARNING] Cannot save - config path not set")
            return
        
        try:
            # Read the current config file
            with open(self._config_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Format the timestamp
            ts_str = timestamp.isoformat()
            
            # Update or add the last_daily_restart line
            import re
            if 'last_daily_restart' in content:
                # Replace existing line
                content = re.sub(
                    r'last_daily_restart\s*=\s*"[^"]*"',
                    f'last_daily_restart = "{ts_str}"',
                    content
                )
            else:
                # Add after daily_restart_minute
                content = re.sub(
                    r'(daily_restart_minute\s*=\s*\d+)',
                    f'\\1\n\n# Last daily restart timestamp (auto-updated by bot)\nlast_daily_restart = "{ts_str}"',
                    content
                )
            
            # Write back
            with open(self._config_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            # Update in-memory value
            self.last_daily_restart = ts_str
            print(f"[CONFIG] Saved last_daily_restart: {ts_str}")
            
        except (IOError, OSError) as e:
            print(f"[WARNING] Failed to save last restart: {e}")
    
    def get_last_daily_restart(self) -> Optional[datetime]:
        """Parse and return the last daily restart as datetime."""
        if not self.last_daily_restart:
            return None
        
        try:
            dt = datetime.fromisoformat(self.last_daily_restart)
            return dt
        except (ValueError, TypeError):
            return None
