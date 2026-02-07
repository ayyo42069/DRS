"""
Window management - find and interact with game window via ADB.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Lazy import helper
_cv2 = None
_np = None


def _get_cv2_np():
    """Lazy load cv2 and numpy."""
    global _cv2, _np
    if _cv2 is None:
        import cv2
        import numpy
        _cv2 = cv2
        _np = numpy
    return _cv2, _np


@dataclass
class WindowInfo:
    """Information about a window."""
    title: str
    x: int
    y: int
    width: int
    height: int


class AdbController:
    """Handles ADB connection and input."""
    
    # Timeout constants (seconds)
    CONNECT_TIMEOUT = 10
    QUICK_TIMEOUT = 5      # For simple queries
    NORMAL_TIMEOUT = 10    # For standard operations
    SCREENCAP_TIMEOUT = 10 # For screen capture
    
    def __init__(self, device_address: str = "127.0.0.1:7555"):
        self.device_address = device_address
        self.connected = False
        self.width = 0
        self.height = 0
    
    def _run_shell(self, *args: str, timeout: Optional[int] = None, text: bool = True) -> Optional[subprocess.CompletedProcess]:
        """Run an ADB shell command with standard error handling."""
        if not self.connected:
            return None
        
        cmd = ["adb", "-s", self.device_address, "shell", *args]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=text,
                check=False,
                timeout=timeout or self.NORMAL_TIMEOUT
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[ADB] Command failed: {e}")
            return None
        
    def connect(self) -> bool:
        """Connect to ADB device."""
        try:
            print(f"[ADB] Connecting to {self.device_address}...")
            
            # Disconnect first to ensure clean state
            subprocess.run(
                ["adb", "disconnect", self.device_address],
                capture_output=True,
                check=False,
                timeout=self.CONNECT_TIMEOUT
            )
            
            # Connect
            result = subprocess.run(
                ["adb", "connect", self.device_address],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.CONNECT_TIMEOUT
            )
            
            output_lower = result.stdout.lower()
            if "connected" in output_lower or "already connected" in output_lower:
                print(f"[ADB] Connected to {self.device_address}")
                self.connected = True
                self.update_resolution()
                return True
            else:
                print(f"[ADB] Failed to connect: {result.stdout.strip()}")
                return False
                
        except FileNotFoundError:
            print("[ADB] adb command not found in PATH")
            return False
        except subprocess.TimeoutExpired:
            print("[ADB] Connection timed out")
            return False
        except OSError as e:
            print(f"[ADB] Connection error: {e}")
            return False
            
    def update_resolution(self) -> None:
        """Get device resolution."""
        result = self._run_shell("wm", "size", timeout=self.QUICK_TIMEOUT)
        if result is None:
            return
        
        try:
            # Output format: "Physical size: 1920x1080"
            if result.returncode == 0 and "size:" in result.stdout:
                parts = result.stdout.strip().split(":")[-1].strip().split("x")
                if len(parts) == 2:
                    self.width = int(parts[0])
                    self.height = int(parts[1])
                    print(f"[ADB] Device resolution: {self.width}x{self.height}")
        except (ValueError, IndexError) as e:
            print(f"[ADB] Failed to parse resolution: {e}")

    def screencap(self) -> Optional[np.ndarray]:
        """
        Capture screen via ADB.
        Uses raw stream (faster) with PNG fallback.
        """
        if not self.connected:
            return None
        
        cv2, np = _get_cv2_np()
        
        # Method 1: Raw Screencap (Fastest - avoids PNG encoding/decoding)
        try:
            cmd = ["adb", "-s", self.device_address, "exec-out", "screencap"]
            result = subprocess.run(cmd, capture_output=True, check=False, timeout=10)
            
            data = result.stdout
            if result.returncode == 0 and len(data) > 12:
                # Header: width (4), height (4), format (4) - all little endian
                w = int.from_bytes(data[0:4], byteorder='little')
                h = int.from_bytes(data[4:8], byteorder='little')
                # format: data[8:12] - not used but parsed for offset
                
                pixels = data[12:]
                expected_len = w * h * 4
                
                if len(pixels) == expected_len:
                    # Reshape and convert RGBA to BGR
                    img = np.frombuffer(pixels, dtype=np.uint8).reshape((h, w, 4))
                    return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        except (subprocess.TimeoutExpired, ValueError) as e:
            print(f"[ADB] Raw screencap failed: {e}")

        # Method 2: PNG Screencap (Compatible fallback)
        try:
            cmd = ["adb", "-s", self.device_address, "exec-out", "screencap", "-p"]
            result = subprocess.run(cmd, capture_output=True, check=False, timeout=10)
            
            if result.returncode == 0 and result.stdout:
                arr = np.frombuffer(result.stdout, np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    return img
            
            return None
            
        except (subprocess.TimeoutExpired, ValueError) as e:
            print(f"[ADB] PNG screencap failed: {e}")
            return None

    def tap(self, x: int, y: int) -> None:
        """Send tap command (fire-and-forget for speed)."""
        if not self.connected:
            return
            
        try:
            # Use Popen to not block - the bot has internal delays for pacing
            subprocess.Popen(
                ["adb", "-s", self.device_address, "shell", "input", "tap", str(x), str(y)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except OSError as e:
            print(f"[ADB] Tap failed: {e}")

    def get_main_activity(self, package_name: str) -> Optional[str]:
        """Get the main launchable activity for a package."""
        if not self.connected:
            return None
        
        try:
            # Method 1: cmd package resolve-activity (Android 7+)
            # Output format:
            # com.package/com.package.Activity
            cmd = ["adb", "-s", self.device_address, "shell", "cmd", "package", "resolve-activity", "--brief", package_name]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
            
            if result.returncode == 0 and result.stdout:
                lines = result.stdout.strip().split('\n')
                # Look for the activity component
                for line in reversed(lines):
                    if '/' in line and package_name in line:
                        return line.strip()
            
            return None
            
        except (subprocess.TimeoutExpired, OSError, IndexError):
            return None

    def launch_app(self, package_name: str) -> bool:
        """Launch an app by package name."""
        if not self.connected:
            return False
            
        try:
            print(f"[ADB] Launching {package_name}...")
            
            # Method 1: Try specific activity launch
            activity = self.get_main_activity(package_name)
            if activity:
                print(f"[ADB] Found main activity: {activity}")
                # Force stop before start to ensure clean launch
                # (Optional, but often helps with 'relaunch' behavior)
                
                cmd = ["adb", "-s", self.device_address, "shell", "am", "start", "-n", activity]
                subprocess.run(cmd, capture_output=True, check=False, timeout=10)
                return True
            
            # Method 2: Monkey (Fallback)
            print("[ADB] Falling back to monkey launch...")
            # Use monkey to launch the app 
            cmd = [
                "adb", "-s", self.device_address, "shell", "monkey",
                "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"
            ]
            subprocess.run(cmd, capture_output=True, check=False, timeout=10)
            return True
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[ADB] Launch failed: {e}")
            return False

    def stop_app(self, package_name: str) -> bool:
        """Force stop an app."""
        print(f"[ADB] Stopping {package_name}...")
        result = self._run_shell("am", "force-stop", package_name)
        return result is not None
    
    def is_app_running(self, package_name: str) -> bool:
        """Check if an app is currently running (has a process)."""
        result = self._run_shell("pidof", package_name, timeout=self.QUICK_TIMEOUT)
        if result is None:
            return False
        # If pidof returns a PID, the app is running
        return result.returncode == 0 and result.stdout.strip().isdigit()
    
    def is_app_foreground(self, package_name: str) -> bool:
        """Check if an app is in the foreground (visible on screen)."""
        # Get the currently focused activity
        result = self._run_shell("dumpsys", "activity", "activities", timeout=self.QUICK_TIMEOUT)
        
        if result and result.returncode == 0:
            # Filter for mResumedActivity in Python instead of shell grep
            for line in result.stdout.split('\n'):
                if 'mResumedActivity' in line and package_name in line:
                    return True
        
        # Fallback: check window focus
        result2 = self._run_shell("dumpsys", "window", "windows", timeout=self.QUICK_TIMEOUT)
        
        if result2 and result2.returncode == 0:
            # Filter for focus indicators in Python
            for line in result2.stdout.split('\n'):
                if ('mCurrentFocus' in line or 'mFocusedApp' in line) and package_name in line:
                    return True

        return False
    
    def get_memory_info(self, package_name: str) -> Optional[int]:
        """Get app memory usage in MB."""
        result = self._run_shell("dumpsys", "meminfo", package_name)
        if result is None or result.returncode != 0:
            return None
        
        try:
            # Look for "TOTAL:" line which shows total memory in KB
            for line in result.stdout.split('\n'):
                if 'TOTAL:' in line.upper() or 'TOTAL ' in line.upper():
                    # Extract the number
                    parts = line.split()
                    for part in parts:
                        if part.isdigit():
                            return int(part) // 1024  # Convert KB to MB
        except ValueError:
            pass
        return None


class WindowManager:
    """Manages game window detection and interaction via ADB."""
    
    # Default resolution if ADB can't detect it
    DEFAULT_WIDTH = 1920
    DEFAULT_HEIGHT = 1080
    
    def __init__(
        self,
        title_pattern: str,  # Kept for API compatibility, not used with ADB
        use_adb: bool = False,
        adb_address: str = "127.0.0.1:7555"
    ):
        # Note: title_pattern is not used in ADB mode but kept for API compatibility
        self.current_window: Optional[WindowInfo] = None
        
        # ADB Support
        self.use_adb = use_adb
        self.adb: Optional[AdbController] = None
        if use_adb:
            self.adb = AdbController(adb_address)
            self.adb.connect()

    def find_window(self) -> Optional[WindowInfo]:
        """Check if ADB is connected and device is available."""
        if self.adb and self.adb.connected:
            # If ADB is connected, create a virtual WindowInfo for compatibility
            if self.adb.width == 0 or self.adb.height == 0:
                self.adb.update_resolution()
                
            self.current_window = WindowInfo(
                title=f"ADB Device ({self.adb.device_address})",
                x=0, 
                y=0, 
                width=self.adb.width if self.adb.width > 0 else self.DEFAULT_WIDTH,
                height=self.adb.height if self.adb.height > 0 else self.DEFAULT_HEIGHT
            )
            return self.current_window
            
        return None
    
    def click(self, x: int, y: int) -> None:
        """Click at position using ADB."""
        if self.adb and self.adb.connected:
            self.adb.tap(x, y)
        else:
            print("[ERROR] Click failed: ADB not connected.")

    def capture_screen(self) -> Optional[np.ndarray]:
        """Capture screen via ADB."""
        if self.use_adb and self.adb and self.adb.connected:
            return self.adb.screencap()
        return None
