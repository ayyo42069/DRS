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
    
    # Connection timeout in seconds
    CONNECT_TIMEOUT = 10
    
    def __init__(self, device_address: str = "127.0.0.1:7555"):
        self.device_address = device_address
        self.connected = False
        self.width = 0
        self.height = 0
        
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
        if not self.connected:
            return
            
        try:
            result = subprocess.run(
                ["adb", "-s", self.device_address, "shell", "wm", "size"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5
            )
            
            # Output format: "Physical size: 1920x1080"
            if result.returncode == 0 and "size:" in result.stdout:
                parts = result.stdout.strip().split(":")[-1].strip().split("x")
                if len(parts) == 2:
                    self.width = int(parts[0])
                    self.height = int(parts[1])
                    print(f"[ADB] Device resolution: {self.width}x{self.height}")
        except (subprocess.TimeoutExpired, ValueError, IndexError) as e:
            print(f"[ADB] Failed to get resolution: {e}")

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

    def launch_app(self, package_name: str) -> bool:
        """Launch an app by package name."""
        if not self.connected:
            return False
            
        try:
            print(f"[ADB] Launching {package_name}...")
            # Use monkey to launch the app (works even if we don't know the main activity)
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
        if not self.connected:
            return False
            
        try:
            print(f"[ADB] Stopping {package_name}...")
            cmd = ["adb", "-s", self.device_address, "shell", "am", "force-stop", package_name]
            subprocess.run(cmd, capture_output=True, check=False, timeout=10)
            return True
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[ADB] Stop failed: {e}")
            return False
    
    def is_app_running(self, package_name: str) -> bool:
        """Check if an app is currently running (has a process)."""
        if not self.connected:
            return False
        
        try:
            cmd = ["adb", "-s", self.device_address, "shell", "pidof", package_name]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
            # If pidof returns a PID, the app is running
            return result.returncode == 0 and result.stdout.strip().isdigit()
        except (subprocess.TimeoutExpired, OSError):
            return False
    
    def is_app_foreground(self, package_name: str) -> bool:
        """Check if an app is in the foreground (visible on screen)."""
        if not self.connected:
            return False
        
        try:
            # Get the currently focused activity
            cmd = ["adb", "-s", self.device_address, "shell", 
                   "dumpsys", "activity", "activities", "|", "grep", "mResumedActivity"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5, shell=True)
            
            if result.returncode == 0:
                return package_name in result.stdout
            
            # Fallback: check window focus
            cmd2 = ["adb", "-s", self.device_address, "shell", 
                    "dumpsys", "window", "windows", "|", "grep", "-E", "mCurrentFocus|mFocusedApp"]
            result2 = subprocess.run(cmd2, capture_output=True, text=True, check=False, timeout=5, shell=True)
            return package_name in result2.stdout
            
        except (subprocess.TimeoutExpired, OSError):
            return False
    
    def clear_app_cache(self, package_name: str) -> bool:
        """Clear app cache (not data) to free memory."""
        if not self.connected:
            return False
        
        try:
            print(f"[ADB] Clearing cache for {package_name}...")
            # This clears cache without affecting user data
            cmd = ["adb", "-s", self.device_address, "shell", "pm", "clear", "--cache-only", package_name]
            result = subprocess.run(cmd, capture_output=True, check=False, timeout=10)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[ADB] Clear cache failed: {e}")
            return False
    
    def get_memory_info(self, package_name: str) -> Optional[int]:
        """Get app memory usage in MB."""
        if not self.connected:
            return None
        
        try:
            cmd = ["adb", "-s", self.device_address, "shell", "dumpsys", "meminfo", package_name]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)
            
            if result.returncode == 0:
                # Look for "TOTAL:" line which shows total memory in KB
                for line in result.stdout.split('\n'):
                    if 'TOTAL:' in line.upper() or 'TOTAL ' in line.upper():
                        # Extract the number
                        parts = line.split()
                        for part in parts:
                            if part.isdigit():
                                return int(part) // 1024  # Convert KB to MB
            return None
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None


class WindowManager:
    """Manages game window detection and interaction via ADB."""
    
    # Default resolution if ADB can't detect it
    DEFAULT_WIDTH = 1920
    DEFAULT_HEIGHT = 1080
    
    def __init__(
        self,
        title_pattern: str,
        use_adb: bool = False,
        adb_address: str = "127.0.0.1:7555"
    ):
        self.title_pattern = title_pattern
        self.current_window: Optional[WindowInfo] = None
        
        # ADB Support
        self.use_adb = use_adb
        self.adb: Optional[AdbController] = None
        if use_adb:
            self.adb = AdbController(adb_address)
            self.adb.connect()

    def find_window(self, debug: bool = True) -> Optional[WindowInfo]:
        """Check if ADB is connected and device is online."""
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
