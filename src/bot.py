"""
Main bot logic - orchestrates all modules.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional, Tuple, TYPE_CHECKING

# Lazy-loaded modules
_cv2 = None
_np = None


def _get_cv2():
    """Lazy load cv2."""
    global _cv2
    if _cv2 is None:
        import cv2
        _cv2 = cv2
    return _cv2


def _get_np():
    """Lazy load numpy."""
    global _np
    if _np is None:
        import numpy
        _np = numpy
    return _np

if TYPE_CHECKING:
    import numpy as np

from .config import Config
from .window import WindowManager
from .matcher import TemplateMatcher, MatchResult
from .fuel import FuelReader
from .discord_notifier import DiscordNotifier


class ScreenState(Enum):
    """Possible states of the game screen."""
    AUTORACE = auto()
    RACE_CARD = auto()
    MAIN_MENU = auto()
    ERROR = auto()
    UNKNOWN = auto()


class DRSBot:
    """
    DRS Auto Race Bot.
    
    Orchestrates screen capture, template matching, fuel reading,
    and game automation.
    """
    
    # Fuel region detection constants (as percentages of screen)
    FUEL_REGION_TOP_PERCENT = 0.20
    FUEL_REGION_LEFT_PERCENT = 0.75
    
    # Fuel OCR crop offsets (in pixels at 1920 width)
    # Need to capture both:
    # - Low fuel (1-4): timer displayed, format "X/ 3:00" - number is far left
    # - Normal fuel (5+): format "XX/75" - number is closer to icon
    # Solution: Start far left, use wide crop to capture both cases
    FUEL_CROP_LEFT_OFFSET = 175  # Start far left to catch timer-shifted fuel
    FUEL_CROP_WIDTH = 120  # Wide enough to capture both formats
    FUEL_CROP_VERTICAL_PADDING = 10
    
    # Log interval during waits (seconds)
    WAIT_LOG_INTERVAL = 30
    ERROR_CHECK_INTERVAL = 10
    
    # Daily restart timezone (UTC+1)
    DAILY_RESTART_TIMEZONE = timezone(timedelta(hours=1))
    
    # Delay constants (seconds)
    ERROR_RECOVERY_DELAY = 3
    FUEL_RETRY_DELAY = 10
    POST_CLICK_DELAY = 2
    ADB_LAUNCH_VERIFY_DELAY = 10
    DISCONNECT_RETRY_DELAY = 5
    ERROR_REPEAT_DELAY = 3
    
    def __init__(self, config: Config):
        self.config = config
        
        # Core modules
        self.window = WindowManager(
            config.window_title,
            use_adb=config.adb_enabled,
            adb_address=config.adb_address
        )
        self.matcher = TemplateMatcher(
            config.templates_dir,
            config.scales,
            config.match_threshold,
            config.target_width
        )
        self.fuel_reader = FuelReader(max_fuel=config.max_fuel)
        
        # Discord notifier
        self.discord = DiscordNotifier(
            config.discord_webhook_url,
            config.discord_enabled
        )
        
        # State
        self.running = False
        self.paused = False
        self.races = 0
        self.current_fuel = 0
        self._start_time = 0.0
        
        # Daily restart tracking
        self._last_daily_restart: Optional[datetime] = config.get_last_daily_restart()
        if self._last_daily_restart:
            print(f"[CONFIG] Loaded last daily restart: {self._last_daily_restart.isoformat()}")
    
    def log(self, message: str, level: str = "INFO") -> None:
        """Print a log message with timestamp."""
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")
    
    def _get_current_time_utc1(self) -> datetime:
        """Get current time in UTC+1 timezone."""
        return datetime.now(self.DAILY_RESTART_TIMEZONE)
    
    def _should_daily_restart(self) -> bool:
        """
        Check if it's time for the daily scheduled restart.
        
        Returns True if:
        - Daily restart is enabled in config
        - Current time is at or past the configured restart time (UTC+1)
        - We haven't done a restart today yet
        """
        # Check if daily restart is enabled
        if not self.config.daily_restart_enabled:
            return False
        
        now = self._get_current_time_utc1()
        
        # Target time for today from config
        target_today = now.replace(
            hour=self.config.daily_restart_hour,
            minute=self.config.daily_restart_minute,
            second=0,
            microsecond=0
        )
        
        # If it's before the scheduled time, no restart needed
        if now < target_today:
            return False
        
        # If we've never done a restart, do it now (if past scheduled time)
        if self._last_daily_restart is None:
            return True
        
        # Check if the last restart was before today's scheduled time
        # (meaning we haven't restarted today yet)
        return self._last_daily_restart < target_today
    
    async def perform_daily_restart(self) -> bool:
        """
        Perform the scheduled daily game restart.
        
        Stops the game via ADB and relaunches it.
        
        Returns:
            True if restart was successful
        """
        if not (self.config.adb_enabled and self.window.adb and self.window.adb.connected):
            self.log("Daily restart skipped - ADB not available", "WARNING")
            return False
        
        now = self._get_current_time_utc1()
        self.log(f"=== DAILY SCHEDULED RESTART ({now.strftime('%Y-%m-%d %H:%M')} UTC+1) ===", "INFO")
        
        # Check memory usage before restart
        mem_before = self.window.adb.get_memory_info(self.config.package_name)
        if mem_before:
            self.log(f"App memory before restart: {mem_before} MB", "INFO")
        
        # Capture screenshot before restart
        pre_screen = self.capture_screen()
        
        # Send Discord notification
        if self.config.discord_send_errors:
            mem_str = f"{mem_before} MB" if mem_before else "Unknown"
            await self.discord.notify_debug(
                "🔄 Daily Scheduled Restart",
                f"Performing daily game restart.\n"
                f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC+1\n"
                f"Total races this session: {self.races}\n"
                f"Memory usage: {mem_str}",
                screenshot=pre_screen
            )
        
        # Stop the game
        self.log("Stopping game for daily restart...", "INFO")
        self.window.adb.stop_app(self.config.package_name)
        await asyncio.sleep(3)
        
        # Verify app is stopped
        if self.window.adb.is_app_running(self.config.package_name):
            self.log("App still running, force stopping again...", "WARNING")
            self.window.adb.stop_app(self.config.package_name)
            await asyncio.sleep(2)
        
        # Launch the game
        self.log("Relaunching game...", "INFO")
        if not self.window.adb.launch_app(self.config.package_name):
            self.log("Failed to launch game!", "ERROR")
            return False
        
        # Wait for boot with progress checks
        wait_secs = self.config.boot_wait_seconds
        self.log(f"Waiting {int(wait_secs)}s for game to boot...")
        
        start = time.time()
        while time.time() - start < wait_secs:
            elapsed = int(time.time() - start)
            
            # Log progress
            if elapsed % self.WAIT_LOG_INTERVAL == 0 and elapsed > 0:
                remaining = int(wait_secs - elapsed)
                is_running = self.window.adb.is_app_running(self.config.package_name)
                status = "running" if is_running else "starting..."
                self.log(f"Boot wait... {remaining}s left (app: {status})")
            
            await asyncio.sleep(1)
        
        # Update last restart time (both in memory and config.toml)
        self._last_daily_restart = now
        self.config.save_last_restart(now)
        
        # Verify game is running
        if await self.find_game(auto_launch=False):
            self.log("Daily restart successful!", "INFO")
            
            # Capture post-restart screenshot
            post_screen = self.capture_screen()
            
            if self.config.discord_send_errors:
                await self.discord.notify_debug(
                    "✅ Daily Restart Complete",
                    f"Game successfully restarted.\n"
                    f"Bot will resume automation.",
                    screenshot=post_screen
                )
            return True
        else:
            self.log("Daily restart failed - game not detected after launch!", "ERROR")
            return False
    
    def capture_screen(self, focus: bool = False) -> Optional[np.ndarray]:
        """
        Capture the game window.
        
        Args:
            focus: If True, bring window to foreground before capture
        """
        # Use ADB capture
        adb_screen = self.window.capture_screen()
        if adb_screen is not None:
            return adb_screen
        return None
    
    def find_template(
        self,
        screen: np.ndarray,
        name: str,
        retries: int = 1,
        delay: float = 0.3
    ) -> Optional[MatchResult]:
        """Find a template with optional retries."""
        current_screen = screen
        
        for attempt in range(retries):
            # Increase threshold tolerance on retries
            threshold_mod = attempt * 0.05
            
            result = self.matcher.find(current_screen, name, threshold_mod)
            if result:
                return result
            
            if attempt < retries - 1:
                time.sleep(delay)
                new_screen = self.capture_screen()
                if new_screen is None:
                    return None
                current_screen = new_screen
        
        return None
    
    def click_template(
        self,
        name: str,
        retries: int = 3,
        delay: float = 0.3
    ) -> bool:
        """Find and click a template."""
        screen = self.capture_screen()
        if screen is None:
            return False
        
        result = self.find_template(screen, name, retries, delay)
        if result:
            self.log(f"Clicking {name} at ({result.x}, {result.y}) conf={result.confidence:.2f}")
            self.window.click(result.x, result.y)
            return True
        
        return False
    
    def get_fuel(self, screen: Optional[np.ndarray] = None) -> Optional[int]:
        """Get current fuel level."""
        np = _get_np()
        
        if screen is None:
            screen = self.capture_screen()
        if screen is None:
            return None
        
        h, w = screen.shape[:2]
        
        # Create a copy to mask out unwanted areas
        # We must preserve the full screen dimensions so the matcher's resolution scaling works correctly
        search_screen = screen.copy()
        
        # Black out bottom portion (keep top for fuel display)
        search_screen[int(h * self.FUEL_REGION_TOP_PERCENT):, :] = 0
        
        # Black out right portion (keep left where fuel is)
        search_screen[:, int(w * self.FUEL_REGION_LEFT_PERCENT):] = 0
        
        # Find fuel icon using masked full-size image
        result = self.matcher.find(search_screen, "fuel")
        
        if result:
            # Crop region around fuel icon for OCR
            # Fuel text is to the right of the icon
            scale_ratio = w / 1920.0
            
            icon_right = result.x + (result.w // 2)
            
            # Start crop left of the icon to catch "Current /"
            x1 = max(0, int(icon_right - (self.FUEL_CROP_LEFT_OFFSET * scale_ratio)))
            x2 = min(w, int(x1 + (self.FUEL_CROP_WIDTH * scale_ratio)))
            y1 = max(0, int(result.y - (result.h // 2) - (self.FUEL_CROP_VERTICAL_PADDING * scale_ratio)))
            y2 = min(h, int(result.y + (result.h // 2) + (self.FUEL_CROP_VERTICAL_PADDING * scale_ratio)))
            
            crop = screen[y1:y2, x1:x2]
            
            # Save debug crop
            if self.config.save_debug_images:
                self._save_debug_image(crop, "fuel_crop")

            fuel = self.fuel_reader.read(crop)
            if fuel is not None:
                self.current_fuel = fuel
                return fuel
            else:
                # Save failed crop for analysis
                self._save_debug_image(crop, "fuel_crop_FAILED")
        
        return None
    
    def _save_debug_image(self, image: np.ndarray, prefix: str, log_path: bool = False) -> None:
        """Save a debug image (crop or screenshot)."""
        try:
            cv2 = _get_cv2()
            self.config.debug_dir.mkdir(exist_ok=True)
            timestamp = time.strftime("%H%M%S")
            path = self.config.debug_dir / f"{prefix}_{timestamp}.png"
            cv2.imwrite(str(path), image)
            if log_path:
                self.log(f"Debug image saved: {path}")
        except (IOError, OSError) as e:
            self.log(f"Failed to save debug image: {e}", "DEBUG")
    
    def detect_screen_state(self, screen: np.ndarray) -> ScreenState:
        """Detect current screen state."""
        # Check for error dialogs first
        if self.matcher.find(screen, "connection_error"):
            return ScreenState.ERROR
        if self.matcher.find(screen, "error_disconnect"):
            return ScreenState.ERROR
        
        # Check for autorace button
        if self.matcher.find(screen, "autorace_button"):
            return ScreenState.AUTORACE
        
        # Check for race card
        if self.matcher.find(screen, "race_card"):
            return ScreenState.RACE_CARD
        
        # Check for main menu race button
        if self.matcher.find(screen, "race_button"):
            return ScreenState.MAIN_MENU
        
        return ScreenState.UNKNOWN
    
    async def handle_error(self, screen: np.ndarray) -> bool:
        """Handle error popups. Returns True if error was handled."""
        # Check for connection error (has Repeat button)
        if self.matcher.find(screen, "connection_error"):
            self.log("Connection error detected!", "WARNING")
            
            # Send Discord notification
            if self.config.discord_send_errors:
                await self.discord.notify_error(
                    "Connection Error",
                    "Lost connection to server. Attempting to reconnect...",
                    screenshot=screen
                )
            
            # Try clicking repeat button
            for _ in range(10):
                if self.click_template("connection_error_repeat", retries=2):
                    self.log("Clicked Repeat button")
                    await asyncio.sleep(5)
                    
                    # Check if error cleared
                    new_screen = self.capture_screen()
                    if new_screen is not None:
                        if not self.matcher.find(new_screen, "connection_error"):
                            self.log("Connection restored!")
                            return True
                else:
                    await asyncio.sleep(3)
            
            return True
        
        # Check for disconnect popup (has OK button)
        if self.matcher.find(screen, "error_disconnect"):
            self.log("Disconnect error detected!", "WARNING")
            
            # Send Discord notification
            if self.config.discord_send_errors:
                await self.discord.notify_error(
                    "Disconnect",
                    "Game disconnected from server.",
                    screenshot=screen
                )
            
            if self.click_template("error_ok", retries=3):
                self.log("Clicked OK button")
                await asyncio.sleep(3)
                return True
        
        return False
    
    async def do_race(self, fuel: int) -> Tuple[bool, int]:
        """
        Execute autorace sequence.
        Returns: (success, num_races)
        """
        self.log(f"Starting race sequence with {fuel} fuel")
        
        screen = self.capture_screen()
        if screen is None:
            return False, 0
        
        state = self.detect_screen_state(screen)
        self.log(f"Current state: {state.name}")
        
        # Handle based on current state
        if state == ScreenState.ERROR:
            await self.handle_error(screen)
            return False, 0
        
        if state == ScreenState.AUTORACE:
            # Already on autorace screen, click it
            if self.click_template("autorace_button"):
                return await self._wait_for_race(fuel)
        
        elif state == ScreenState.RACE_CARD:
            # Click race card first
            if self.click_template("race_card"):
                await asyncio.sleep(self.config.transition_delay)
                if self.click_template("autorace_button", retries=5):
                    return await self._wait_for_race(fuel)
        
        elif state == ScreenState.MAIN_MENU:
            # Full sequence: race button -> race card -> autorace
            if self.click_template("race_button"):
                await asyncio.sleep(self.config.transition_delay)
                
                if self.click_template("race_card", retries=3):
                    await asyncio.sleep(self.config.transition_delay)
                    
                    if self.click_template("autorace_button", retries=5):
                        return await self._wait_for_race(fuel)
        
        else:
            # Unknown state, try full sequence
            self.log("Unknown state, trying full sequence...", "WARNING")
            
            self.click_template("race_button")
            await asyncio.sleep(self.config.transition_delay)
            
            self.click_template("race_card")
            await asyncio.sleep(self.config.transition_delay)
            
            if self.click_template("autorace_button"):
                return await self._wait_for_race(fuel)
        
        self.log("Race sequence failed", "WARNING")
        return False, 0
    
    async def _wait_for_race(self, fuel: int) -> Tuple[bool, int]:
        """Wait for race to complete."""
        # Calculate race count and wait time
        num_races = min(fuel, self.config.max_fuel) // self.config.fuel_per_race
        race_time = num_races * self.config.race_duration
        total_wait = race_time + self.config.race_buffer_time
        
        self.log(f">>> AUTORACE STARTED ({num_races} races, ~{int(total_wait)}s) <<<")
        
        # Capture screenshot for Discord
        pre_screen = self.capture_screen()
        
        # Send Discord notification
        if self.config.discord_send_race_start:
            await self.discord.notify_race_started(
                num_races=num_races,
                fuel=fuel,
                estimated_time=int(total_wait),
                session_races=self.races,
                screenshot=pre_screen
            )
        
        # Wait with countdown
        start = time.time()
        last_log = 0
        
        while self.running:
            elapsed = time.time() - start
            remaining = total_wait - elapsed
            
            if remaining <= 0:
                break
            
            # Log at intervals
            if int(elapsed) >= last_log + self.WAIT_LOG_INTERVAL:
                last_log = int(elapsed)
                mins, secs = divmod(int(remaining), 60)
                self.log(f"[WAIT] {mins}m {secs}s remaining...")
            
            # Check for errors periodically
            if int(elapsed) % self.ERROR_CHECK_INTERVAL == 0:
                screen = self.capture_screen()
                if screen is not None and self.detect_screen_state(screen) == ScreenState.ERROR:
                    self.log("Error during race!", "WARNING")
                    await self.handle_error(screen)
                    break
            
            await asyncio.sleep(1)
        
        self.log(f">>> RACE COMPLETE ({num_races} races) <<<")
        
        # Capture post-race screenshot
        post_screen = self.capture_screen()
        post_fuel = self.get_fuel(post_screen) or 0
        
        # Send Discord notification
        if self.config.discord_send_race_complete:
            await self.discord.notify_race_complete(
                races_done=num_races,
                total_races=self.races + num_races,
                fuel_after=post_fuel,
                max_fuel=self.config.max_fuel,
                screenshot=post_screen
            )
        
        return True, num_races
    
    async def wait_for_fuel(self, target: int) -> bool:
        """Wait until fuel reaches target."""
        while self.running:
            screen = self.capture_screen()
            fuel = self.get_fuel(screen)
            
            if fuel is not None:
                if fuel >= target:
                    return True
                
                # Calculate wait time
                needed = target - fuel
                wait_secs = int(needed * self.config.fuel_regen_seconds)
                
                mins, secs = divmod(wait_secs, 60)
                hours, mins = divmod(mins, 60)
                
                time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m {secs}s"
                
                self.log(f"[FUEL] {fuel}/{self.config.max_fuel} - Need {needed} more ({time_str} wait)")
                
                # Save debug screenshot if enabled
                if self.config.save_debug_images and screen is not None:
                    self._save_debug_image(screen, "fuel_wait", log_path=True)
                
                # Send Discord notification with screenshot
                if self.config.discord_send_fuel_wait:
                    await self.discord.notify_waiting_fuel(
                        current=fuel,
                        target=target,
                        wait_secs=wait_secs,
                        max_fuel=self.config.max_fuel,
                        session_races=self.races,
                        screenshot=screen
                    )
                
                # Wait with periodic checks (returns True if fuel detected early)
                fuel_ready = await self._fuel_wait(wait_secs)
                if fuel_ready:
                    return True  # Fuel confirmed ready, no need to re-check
            else:
                self.log("Can't read fuel, retrying...", "WARNING")
                
                # Save debug screenshot for failed fuel read
                if self.config.save_debug_images and screen is not None:
                    self._save_debug_image(screen, "fuel_read_failed", log_path=True)
                
                # Send debug notification if enabled
                if self.config.discord_send_errors:
                    await self.discord.notify_debug(
                        "Fuel Read Failed",
                        "Could not read fuel value from screen.\nRetrying in 10 seconds...",
                        screenshot=screen
                    )
                
                # Check for errors
                if screen is not None:
                    if self.detect_screen_state(screen) == ScreenState.ERROR:
                        await self.handle_error(screen)
                
                await asyncio.sleep(10)
        
        return False
    
    async def _fuel_wait(self, total_secs: float) -> bool:
        """
        Wait for fuel with periodic status updates.
        
        Returns:
            True if fuel is ready (detected early), False if wait completed normally.
        """
        start = time.time()
        last_log = 0
        last_check = 0
        check_interval = min(60, self.config.fuel_check_interval)
        
        while self.running and not self.paused:
            elapsed = time.time() - start
            remaining = total_secs - elapsed
            
            if remaining <= 0:
                return False  # Wait completed, fuel should be ready
            
            # Log every minute
            if int(elapsed) >= last_log + 60:
                last_log = int(elapsed)
                mins, secs = divmod(int(remaining), 60)
                self.log(f"[WAIT] {mins}m {secs}s until fuel ready...")
            
            # Periodic fuel check to catch OCR misreads (only once per interval)
            if elapsed >= last_check + check_interval:
                last_check = elapsed
                fuel = self.get_fuel()
                if fuel is not None and fuel >= self.config.min_fuel:
                    self.log(f"[CHECK] Fuel ready early: {fuel}")
                    return True  # Fuel is ready
            
            await asyncio.sleep(1)
        
        return False  # Interrupted
    
    async def find_game(self, auto_launch: bool = True) -> bool:
        """
        Find and verify game window.
        
        Args:
            auto_launch: If True, automatically launch game if not found
        """
        self.log(f"Searching for window: '{self.config.window_title}'")
        
        window = self.window.find_window()
        
        # If window not found, try to launch the game
        if window is None:
            self.log("Game window not found!", "WARNING")
            
            if auto_launch:
                self.log("Attempting to launch game...", "INFO")
                
                # Method 1: ADB Launch
                if self.config.adb_enabled and self.window.adb and self.window.adb.connected:
                    self.log("Launching via ADB...")
                    self.window.adb.launch_app(self.config.package_name)
                    # Wait for boot
                    wait_secs = self.config.boot_wait_seconds
                    self.log(f"Waiting {int(wait_secs)}s for game to boot...")
                    await asyncio.sleep(wait_secs)
                    
                    # Try finding window again
                    window = self.window.find_window()
                else:
                    self.log("ADB not configured or connected", "ERROR")
                    return False
                    
                if window is None:
                    self.log("Game launched but window still not found!", "ERROR")
                    return False
            else:
                self.log("Auto-launch disabled", "ERROR")
                return False
        
        self.log(f"Found: {window.title} ({window.width}x{window.height})")
        
        # Verify game is actually running by looking for templates
        screen = self.capture_screen()
        if screen is None:
            self.log("Failed to capture window!", "ERROR")
            return False
        
        # Look for ANY known game element
        verification_templates = ["fuel", "race_button", "race_card", "autorace_button"]
        
        for template_name in verification_templates:
            result = self.matcher.find(screen, template_name, threshold_modifier=0.15)
            if result:
                self.log(f"Game verified! Found {template_name} (conf={result.confidence:.2f})")
                return True
        
        # If no templates found, game might not be fully loaded
        if auto_launch:
            self.log("Window found but game not detected - trying to launch game...", "WARNING")
            
            # Save debug screenshot
            if self.config.save_debug_images:
                self._save_debug_image(screen, "game_not_detected", log_path=True)
            
            # Launch the game
            if self.config.adb_enabled and self.window.adb:
                self.window.adb.launch_app(self.config.package_name)
                await asyncio.sleep(10)
                
                # Recapture and verify
                screen = self.capture_screen()
                if screen is not None:
                    for template_name in verification_templates:
                        result = self.matcher.find(screen, template_name, threshold_modifier=0.15)
                        if result:
                            self.log(f"Game verified after launch! Found {template_name}")
                            return True
        
        # If still not found, save debug screenshot
        if self.config.save_debug_images and screen is not None:
            self._save_debug_image(screen, "game_not_found", log_path=True)
        
        self.log("Window found but game not detected - templates not matching", "WARNING")
        self.log("Check if game resolution changed or templates need updating", "WARNING")
        
        return False
    

    
    async def run(self) -> None:
        """Main bot loop."""
        self.log("=" * 50)
        self.log("DRS Auto Race Bot v4.0")
        self.log("=" * 50)
        
        # List available templates
        templates = self.matcher.list_templates()
        self.log(f"Templates: {', '.join(templates)}")
        
        # Check required templates
        required = ["race_button", "race_card", "autorace_button", "fuel"]
        missing = [t for t in required if t not in templates]
        if missing:
            self.log(f"Missing templates: {missing}", "ERROR")
            return
        
        # Find game (will auto-launch if not running)
        if not await self.find_game():
            self.log("Could not find or launch the game. Check ADB connection.", "ERROR")
            return
        
        self.running = True
        self.races = 0
        self._start_time = time.time()
        
        # Get initial fuel
        initial_fuel = self.get_fuel() or 0
        
        # Send Discord notification for bot started
        if self.config.discord_enabled:
            await self.discord.notify_started(
                window_title=self.window.current_window.title if self.window.current_window else "Unknown",
                fuel=initial_fuel,
                max_fuel=self.config.max_fuel,
                adb_address=self.config.adb_address if self.config.adb_enabled else None
            )
        
        self.log("Bot started! Press Ctrl+C to stop.")
        self.log("-" * 50)
        
        try:
            while self.running:
                # Pause handling
                while self.paused and self.running:
                    await asyncio.sleep(0.5)
                
                if not self.running:
                    break
                
                # Check for daily scheduled restart (6 AM UTC+1)
                if self._should_daily_restart():
                    await self.perform_daily_restart()
                    continue
                
                # Capture and analyze
                screen = self.capture_screen()
                if screen is None:
                    self.log("Capture failed, retrying...", "WARNING")
                    await asyncio.sleep(2)
                    continue
                
                # Check for errors
                state = self.detect_screen_state(screen)
                if state == ScreenState.ERROR:
                    await self.handle_error(screen)
                    await asyncio.sleep(3)
                    continue
                
                # Get fuel
                fuel = self.get_fuel(screen)
                if fuel is None:
                    self.log("Can't read fuel", "WARNING")
                    
                    # Save debug screenshot
                    if self.config.save_debug_images:
                        self._save_debug_image(screen, "main_fuel_read_failed", log_path=True)
                    
                    # Send debug notification with screenshot
                    if self.config.discord_send_errors:
                        await self.discord.notify_debug(
                            "Fuel Read Failed",
                            "Could not read fuel value in main loop.\nScreen state: " + state.name,
                            screenshot=screen
                        )
                    
                    await asyncio.sleep(2)
                    continue
                
                # Check if enough fuel to race
                if fuel >= self.config.min_fuel:
                    self.log(f"[FUEL] {fuel}/{self.config.max_fuel} - Racing!")
                    
                    success, batch = await self.do_race(fuel)
                    if success:
                        self.races += batch
                        self.log(f"[TOTAL] Session: {self.races} races")
                    else:
                        self.log("Race failed, will retry...", "WARNING")
                        await asyncio.sleep(2)
                else:
                    self.log(f"[FUEL] {fuel}/{self.config.min_fuel} needed - Waiting")
                    await self.wait_for_fuel(self.config.min_fuel)
        
        except KeyboardInterrupt:
            self.log("Interrupted by user")
        except (RuntimeError, ValueError, OSError) as e:
            self.log(f"Error: {e}", "ERROR")
            traceback.print_exc()
        finally:
            self.running = False
            await self.cleanup()
    
    async def cleanup(self) -> None:
        """Clean up resources."""
        uptime = int(time.time() - self._start_time)
        mins, secs = divmod(uptime, 60)
        
        self.log("-" * 50)
        self.log(f"Total races: {self.races}")
        self.log(f"Uptime: {mins}m {secs}s")
        self.log("Bot stopped.")
        
        # Send Discord notification
        if self.config.discord_enabled:
            await self.discord.notify_stopped(
                total_races=self.races,
                uptime_secs=uptime
            )
            await self.discord.close()
        
        self.matcher.clear_cache()
    
    def stop(self) -> None:
        """Stop the bot."""
        self.log("Stopping...")
        self.running = False
