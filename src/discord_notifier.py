"""
Discord webhook notifications with screenshot support.
Enhanced with rich embeds, timestamps, and detailed stats.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Lazy-loaded modules
_session = None
_cv2 = None

# Bot version
BOT_VERSION = "4.1"


def _get_cv2():
    """Lazy load cv2."""
    global _cv2
    if _cv2 is None:
        import cv2
        _cv2 = cv2
    return _cv2


async def _get_session():
    """Lazy load aiohttp session."""
    global _session
    if _session is None:
        import aiohttp
        _session = aiohttp.ClientSession()
    return _session


async def close_session() -> None:
    """Close the aiohttp session."""
    global _session
    if _session is not None:
        await _session.close()
        _session = None


class DiscordNotifier:
    """Discord webhook notifier with screenshot support and rich embeds."""
    
    # HTTP status codes that indicate success
    SUCCESS_CODES = (200, 204)
    
    # Embed colors (Discord uses decimal, not hex in JSON)
    COLOR_SUCCESS = 0x2ECC71    # Emerald green
    COLOR_RACING = 0x3498DB     # Blue
    COLOR_WARNING = 0xF39C12    # Orange
    COLOR_ERROR = 0xE74C3C      # Red
    COLOR_DEBUG = 0x9B59B6      # Purple
    COLOR_STOPPED = 0x95A5A6    # Gray
    COLOR_INFO = 0x1ABC9C       # Teal
    
    # Timezone for timestamps (UTC+1)
    TIMEZONE = timezone(timedelta(hours=1))
    
    def __init__(self, webhook_url: str, enabled: bool = True):
        self.webhook_url = webhook_url
        self.enabled = enabled and bool(webhook_url)
        self._start_time = time.time()
        self._total_races = 0
        if self.enabled:
            print("[OK] Discord notifications enabled")
    
    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format for Discord."""
        return datetime.now(self.TIMEZONE).isoformat()
    
    def _get_uptime_str(self) -> str:
        """Get formatted uptime string."""
        uptime = int(time.time() - self._start_time)
        hours, remainder = divmod(uptime, 3600)
        mins, secs = divmod(remainder, 60)
        
        if hours > 0:
            return f"{hours}h {mins}m {secs}s"
        elif mins > 0:
            return f"{mins}m {secs}s"
        else:
            return f"{secs}s"
    
    def _encode_image(self, image: np.ndarray) -> Optional[bytes]:
        """Encode numpy image to PNG bytes."""
        if image is None:
            return None
        try:
            cv2 = _get_cv2()
            _, buffer = cv2.imencode('.png', image)
            return buffer.tobytes()
        except (ValueError, RuntimeError) as e:
            print(f"[WARNING] Failed to encode image: {e}")
            return None
    
    def _create_embed(
        self,
        title: str,
        description: Optional[str] = None,
        color: int = COLOR_SUCCESS,
        fields: Optional[List[Dict[str, Any]]] = None,
        footer: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        has_image: bool = False,
        add_timestamp: bool = True
    ) -> Dict[str, Any]:
        """Create a Discord embed object with rich formatting."""
        embed: Dict[str, Any] = {
            "title": title,
            "color": color
        }
        
        if description:
            embed["description"] = description
        
        if fields:
            embed["fields"] = fields
        
        # Add timestamp
        if add_timestamp:
            embed["timestamp"] = self._get_timestamp()
        
        # Footer with bot info
        footer_text = footer or f"DRS Bot v{BOT_VERSION}"
        embed["footer"] = {
            "text": f"{footer_text} • Uptime: {self._get_uptime_str()}"
        }
        
        if thumbnail_url:
            embed["thumbnail"] = {"url": thumbnail_url}
        
        if has_image:
            embed["image"] = {"url": "attachment://screenshot.png"}
        
        return embed
    
    async def send(
        self,
        content: str = "",
        embed: Optional[Dict[str, Any]] = None,
        image: Optional[np.ndarray] = None
    ) -> None:
        """Send a message to Discord with optional screenshot."""
        if not self.enabled:
            return
        
        try:
            import aiohttp
            import json
            
            session = await _get_session()
            
            # Prepare form data
            data = aiohttp.FormData()
            
            # Add JSON payload
            payload: Dict[str, Any] = {}
            if content:
                payload["content"] = content
            if embed:
                # If we have an image, reference it in the embed
                if image is not None:
                    embed["image"] = {"url": "attachment://screenshot.png"}
                payload["embeds"] = [embed]
            
            if payload:
                data.add_field("payload_json", json.dumps(payload))
            
            # Add image if provided
            if image is not None:
                img_bytes = self._encode_image(image)
                if img_bytes:
                    data.add_field(
                        "file",
                        img_bytes,
                        filename="screenshot.png",
                        content_type="image/png"
                    )
            
            async with session.post(self.webhook_url, data=data) as resp:
                if resp.status not in self.SUCCESS_CODES:
                    text = await resp.text()
                    print(f"[WARNING] Discord webhook failed: {resp.status} - {text[:100]}")
                    
        except (aiohttp.ClientError, ValueError, RuntimeError) as e:
            print(f"[WARNING] Discord notification failed: {e}")
    
    async def notify_started(
        self,
        window_title: str,
        fuel: int,
        max_fuel: int,
        adb_address: Optional[str] = None
    ) -> None:
        """Notify bot has started with detailed info."""
        self._start_time = time.time()
        self._total_races = 0
        
        now = datetime.now(self.TIMEZONE)
        
        fields = [
            {"name": "🎮 Device", "value": f"`{window_title}`", "inline": False},
            {"name": "⛽ Fuel", "value": f"**{fuel}** / {max_fuel}", "inline": True},
            {"name": "📅 Started", "value": now.strftime("%Y-%m-%d %H:%M"), "inline": True},
        ]
        
        if adb_address:
            fields.append({"name": "📱 ADB", "value": f"`{adb_address}`", "inline": True})
        
        embed = self._create_embed(
            title="🚀 Bot Started",
            description="Automation is now running. Will notify on race completions and errors.",
            color=self.COLOR_SUCCESS,
            fields=fields,
            footer=f"DRS Auto Race Bot v{BOT_VERSION}"
        )
        await self.send(embed=embed)
    
    async def notify_race_started(
        self,
        num_races: int,
        fuel: int,
        estimated_time: int,
        session_races: int = 0,
        screenshot: Optional[np.ndarray] = None
    ) -> None:
        """Notify that races have started with detailed timing."""
        mins, secs = divmod(estimated_time, 60)
        
        # Calculate expected completion time
        now = datetime.now(self.TIMEZONE)
        finish_time = now + timedelta(seconds=estimated_time)
        
        fields = [
            {"name": "🏁 Races", "value": f"**{num_races}**", "inline": True},
            {"name": "⛽ Fuel Used", "value": f"**{fuel}**", "inline": True},
            {"name": "⏱️ Duration", "value": f"{mins}m {secs}s", "inline": True},
            {"name": "🕐 Started", "value": now.strftime("%H:%M:%S"), "inline": True},
            {"name": "🏆 Est. Finish", "value": finish_time.strftime("%H:%M:%S"), "inline": True},
            {"name": "📊 Session Total", "value": f"{session_races} races", "inline": True},
        ]
        
        embed = self._create_embed(
            title="🏎️ Autorace Started!",
            description=f"Racing **{num_races}** races using **{fuel}** fuel.",
            color=self.COLOR_RACING,
            fields=fields
        )
        await self.send(embed=embed, image=screenshot)
    
    async def notify_race_complete(
        self,
        races_done: int,
        total_races: int,
        fuel_after: int,
        max_fuel: int = 75,
        screenshot: Optional[np.ndarray] = None
    ) -> None:
        """Notify that a race batch completed with stats."""
        self._total_races = total_races
        
        # Calculate fuel percentage
        fuel_pct = int((fuel_after / max_fuel) * 100) if max_fuel > 0 else 0
        fuel_bar = self._create_progress_bar(fuel_pct)
        
        fields = [
            {"name": "✅ Batch Complete", "value": f"**{races_done}** races", "inline": True},
            {"name": "📊 Session Total", "value": f"**{total_races}** races", "inline": True},
            {"name": "⏱️ Uptime", "value": self._get_uptime_str(), "inline": True},
            {"name": f"⛽ Fuel ({fuel_after}/{max_fuel})", "value": fuel_bar, "inline": False},
        ]
        
        embed = self._create_embed(
            title="✅ Races Complete!",
            description=f"Successfully completed **{races_done}** races.",
            color=self.COLOR_SUCCESS,
            fields=fields
        )
        await self.send(embed=embed, image=screenshot)
    
    def _create_progress_bar(self, percentage: int, length: int = 10) -> str:
        """Create a visual progress bar."""
        filled = int(length * percentage / 100)
        empty = length - filled
        bar = "█" * filled + "░" * empty
        return f"`{bar}` {percentage}%"
    
    async def notify_waiting_fuel(
        self,
        current: int,
        target: int,
        wait_secs: int,
        max_fuel: int = 75,
        session_races: int = 0,
        screenshot: Optional[np.ndarray] = None
    ) -> None:
        """Notify waiting for fuel with detailed timing."""
        mins, secs = divmod(wait_secs, 60)
        hours, mins = divmod(mins, 60)
        
        if hours > 0:
            time_str = f"{hours}h {mins}m"
        else:
            time_str = f"{mins}m {secs}s"
        
        # Calculate when fuel will be ready
        now = datetime.now(self.TIMEZONE)
        ready_time = now + timedelta(seconds=wait_secs)
        
        # Fuel progress bar
        fuel_pct = int((current / max_fuel) * 100) if max_fuel > 0 else 0
        fuel_bar = self._create_progress_bar(fuel_pct)
        
        fields = [
            {"name": f"⛽ Fuel ({current}/{max_fuel})", "value": fuel_bar, "inline": False},
            {"name": "🎯 Need", "value": f"**{target}** fuel", "inline": True},
            {"name": "⏳ Wait Time", "value": f"**{time_str}**", "inline": True},
            {"name": "🕐 Ready At", "value": ready_time.strftime("%H:%M"), "inline": True},
            {"name": "📊 Session Races", "value": f"{session_races}", "inline": True},
            {"name": "⏱️ Uptime", "value": self._get_uptime_str(), "inline": True},
        ]
        
        embed = self._create_embed(
            title="⏳ Waiting for Fuel",
            description=f"Low on fuel. Will resume racing when **{target}** fuel is available.",
            color=self.COLOR_WARNING,
            fields=fields
        )
        await self.send(embed=embed, image=screenshot)
    
    async def notify_debug(
        self,
        title: str,
        message: str,
        screenshot: Optional[np.ndarray] = None,
        fields: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Send a debug notification with optional screenshot."""
        all_fields = fields or []
        all_fields.append({"name": "📊 Session Races", "value": str(self._total_races), "inline": True})
        all_fields.append({"name": "⏱️ Uptime", "value": self._get_uptime_str(), "inline": True})
        
        embed = self._create_embed(
            title=f"🔧 {title}",
            description=message,
            color=self.COLOR_DEBUG,
            fields=all_fields
        )
        await self.send(embed=embed, image=screenshot)
    
    async def notify_error(
        self,
        error_type: str,
        message: str,
        screenshot: Optional[np.ndarray] = None,
        session_races: int = 0
    ) -> None:
        """Notify an error occurred with context."""
        now = datetime.now(self.TIMEZONE)
        
        fields = [
            {"name": "🕐 Time", "value": now.strftime("%H:%M:%S"), "inline": True},
            {"name": "📊 Session Races", "value": str(session_races or self._total_races), "inline": True},
            {"name": "⏱️ Uptime", "value": self._get_uptime_str(), "inline": True},
        ]
        
        embed = self._create_embed(
            title=f"⚠️ {error_type}",
            description=message,
            color=self.COLOR_ERROR,
            fields=fields
        )
        await self.send(embed=embed, image=screenshot)
    
    async def notify_stopped(
        self,
        total_races: int,
        uptime_secs: int
    ) -> None:
        """Notify bot has stopped with session summary."""
        mins, secs = divmod(uptime_secs, 60)
        hours, mins = divmod(mins, 60)
        
        if hours > 0:
            time_str = f"{hours}h {mins}m {secs}s"
        else:
            time_str = f"{mins}m {secs}s"
        
        # Calculate races per hour
        if uptime_secs > 0:
            races_per_hour = (total_races / uptime_secs) * 3600
        else:
            races_per_hour = 0
        
        now = datetime.now(self.TIMEZONE)
        
        fields = [
            {"name": "🏆 Total Races", "value": f"**{total_races}**", "inline": True},
            {"name": "⏱️ Total Uptime", "value": f"**{time_str}**", "inline": True},
            {"name": "📈 Races/Hour", "value": f"**{races_per_hour:.1f}**", "inline": True},
            {"name": "🛑 Stopped At", "value": now.strftime("%Y-%m-%d %H:%M"), "inline": False},
        ]
        
        embed = self._create_embed(
            title="🛑 Bot Stopped",
            description="Automation has been stopped. Session summary below.",
            color=self.COLOR_STOPPED,
            fields=fields,
            add_timestamp=True
        )
        await self.send(embed=embed)
    
    async def notify_daily_restart(
        self,
        reason: str,
        session_races: int,
        memory_mb: Optional[int] = None,
        screenshot: Optional[np.ndarray] = None
    ) -> None:
        """Notify about scheduled daily restart."""
        now = datetime.now(self.TIMEZONE)
        
        fields = [
            {"name": "📅 Restart Time", "value": now.strftime("%Y-%m-%d %H:%M"), "inline": True},
            {"name": "📊 Session Races", "value": str(session_races), "inline": True},
            {"name": "⏱️ Uptime", "value": self._get_uptime_str(), "inline": True},
        ]
        
        if memory_mb is not None:
            fields.append({"name": "💾 Memory Usage", "value": f"{memory_mb} MB", "inline": True})
        
        embed = self._create_embed(
            title="🔄 Daily Scheduled Restart",
            description=reason,
            color=self.COLOR_INFO,
            fields=fields
        )
        await self.send(embed=embed, image=screenshot)
    
    async def close(self) -> None:
        """Close the session."""
        await close_session()
