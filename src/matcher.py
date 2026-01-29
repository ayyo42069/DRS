"""
Template matching module - robust multi-scale matching with adaptive thresholds.
"""
from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class MatchResult:
    """Result of a template match."""
    x: int  # Center X
    y: int  # Center Y
    w: int  # Width
    h: int  # Height
    confidence: float
    template_name: str
    scale: float


class TemplateMatcher:
    """
    Multi-scale template matcher with adaptive thresholds.
    
    Features:
    - Adaptive threshold that lowers on retry
    - Multiple scale factors to handle different resolutions
    - Pre-computed scaled templates for performance
    - Memory efficient lazy loading
    """
    
    # Minimum template size after scaling
    MIN_TEMPLATE_SIZE = 10
    
    # Minimum threshold floor (won't go below this even with modifiers)
    MIN_THRESHOLD = 0.3
    
    # Early exit confidence bonus (if this far above threshold, stop searching)
    EARLY_EXIT_BONUS = 0.15
    
    # Suffixes to skip when listing templates
    SKIP_SUFFIXES = ("_bright", "_dark", "_gray", "_scale")
    
    def __init__(
        self,
        templates_dir: Path,
        scales: List[float],
        base_threshold: float,
        target_width: int = 0
    ):
        """
        Initialize the template matcher.
        
        Args:
            templates_dir: Directory containing template PNG files
            scales: List of scale factors to try (e.g., [0.8, 0.9, 1.0, 1.1, 1.2])
            base_threshold: Minimum confidence threshold (0.0 to 1.0)
            target_width: Width to resize screen to before matching (0 = disable)
        """
        self.templates_dir = templates_dir
        self.scales = scales
        self.base_threshold = base_threshold
        self.target_width = target_width
        
        # Cache for loaded templates (lazy loading)
        self._template_cache: Dict[str, np.ndarray] = {}
        self._template_gray_cache: Dict[str, np.ndarray] = {}
        
        # Pre-computed scaled templates: {name: {scale: gray_template}}
        self._scaled_cache: Dict[str, Dict[float, np.ndarray]] = {}
        
        # Cached list of template names (computed once)
        self._template_names: Optional[List[str]] = None
    
    def _load_template(self, name: str) -> Optional[np.ndarray]:
        """Load a template image by name."""
        if name in self._template_cache:
            return self._template_cache[name]
        
        # Try loading the template file
        template_path = self.templates_dir / f"{name}.png"
        if not template_path.exists():
            print(f"[WARNING] Template not found: {template_path}")
            return None
        
        template = cv2.imread(str(template_path))
        if template is None:
            print(f"[WARNING] Failed to load template: {template_path}")
            return None
        
        self._template_cache[name] = template
        # Use max channel for grayscale to handle colored icons (Orange/Red/Yellow become bright)
        self._template_gray_cache[name] = np.max(template, axis=2)
        
        return template
    
    def _get_scaled_template(self, name: str, scale: float) -> Optional[np.ndarray]:
        """Get or create scaled template (cached for performance)."""
        if name not in self._template_gray_cache:
            if self._load_template(name) is None:
                return None
        
        # Initialize cache for this template if needed
        if name not in self._scaled_cache:
            self._scaled_cache[name] = {}
        
        # Return cached scaled version if available
        if scale in self._scaled_cache[name]:
            return self._scaled_cache[name][scale]
        
        # Create and cache scaled version
        gray = self._template_gray_cache[name]
        h, w = gray.shape[:2]
        new_w = int(w * scale)
        new_h = int(h * scale)
        
        if new_w < self.MIN_TEMPLATE_SIZE or new_h < self.MIN_TEMPLATE_SIZE:
            return None
        
        scaled = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
        self._scaled_cache[name][scale] = scaled
        
        return scaled
    
    def _match_at_scale(
        self,
        screen_gray: np.ndarray,
        template_name: str,
        scale: float
    ) -> Tuple[float, Tuple[int, int], int, int]:
        """
        Match template at a specific scale.
        
        Returns: (confidence, (x, y), width, height)
        """
        scaled = self._get_scaled_template(template_name, scale)
        if scaled is None:
            return (0.0, (0, 0), 0, 0)
        
        new_h, new_w = scaled.shape[:2]
        screen_h, screen_w = screen_gray.shape[:2]
        
        if new_w > screen_w or new_h > screen_h:
            return (0.0, (0, 0), new_w, new_h)
        
        # Template matching
        result = cv2.matchTemplate(screen_gray, scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        
        return (max_val, max_loc, new_w, new_h)
    
    def find(
        self,
        screen: np.ndarray,
        template_name: str,
        threshold_modifier: float = 0.0
    ) -> Optional[MatchResult]:
        """
        Find a template in the screen.
        
        Args:
            screen: BGR screen image
            template_name: Name of template (without .png)
            threshold_modifier: Reduce threshold by this amount (for retries)
        
        Returns:
            MatchResult if found, None otherwise
        """
        # Ensure template is loaded
        if self._load_template(template_name) is None:
            return None
            
        # Handle resolution independence
        scale_factor = 1.0
        processing_screen = screen
        
        if self.target_width > 0:
            h, w = screen.shape[:2]
            if w != self.target_width:
                scale_factor = w / self.target_width
                # Resize screen to target width
                new_h = int(h / scale_factor)
                processing_screen = cv2.resize(
                    screen, (self.target_width, new_h), interpolation=cv2.INTER_AREA
                )
        
        # Use max channel for grayscale to handle colored icons
        screen_gray = np.max(processing_screen, axis=2)
        
        effective_threshold = max(self.MIN_THRESHOLD, self.base_threshold - threshold_modifier)
        
        best_match: Optional[MatchResult] = None
        best_conf = 0.0
        
        # Try each scale
        for scale in self.scales:
            conf, loc, w, h = self._match_at_scale(screen_gray, template_name, scale)
            
            if conf > best_conf:
                best_conf = conf
                center_x = loc[0] + w // 2
                center_y = loc[1] + h // 2
                final_w = w
                final_h = h
                
                # Map coordinates back to original resolution if needed
                if scale_factor != 1.0:
                    center_x = int(center_x * scale_factor)
                    center_y = int(center_y * scale_factor)
                    final_w = int(final_w * scale_factor)
                    final_h = int(final_h * scale_factor)
                
                best_match = MatchResult(
                    x=center_x,
                    y=center_y,
                    w=final_w,
                    h=final_h,
                    confidence=conf,
                    template_name=template_name,
                    scale=scale
                )
                
                # Early exit on high confidence
                if conf >= effective_threshold + self.EARLY_EXIT_BONUS:
                    break
        
        # Check if best match exceeds threshold
        if best_match and best_match.confidence >= effective_threshold:
            return best_match
        
        return None
    
    def find_any(
        self,
        screen: np.ndarray,
        template_names: List[str],
        threshold_modifier: float = 0.0
    ) -> Optional[MatchResult]:
        """
        Find any of the given templates.
        Returns the first match found (best confidence if multiple match).
        """
        best: Optional[MatchResult] = None
        
        for name in template_names:
            result = self.find(screen, name, threshold_modifier)
            if result:
                if best is None or result.confidence > best.confidence:
                    best = result
                    # Early exit on very high confidence
                    if best.confidence >= self.base_threshold + 0.2:
                        return best
        
        return best
    
    def list_templates(self) -> List[str]:
        """List available template names (cached)."""
        if self._template_names is not None:
            return self._template_names
        
        if not self.templates_dir.exists():
            self._template_names = []
            return self._template_names
        
        templates = []
        for f in self.templates_dir.glob("*.png"):
            name = f.stem
            # Skip augmented variants
            if not any(name.endswith(suffix) for suffix in self.SKIP_SUFFIXES):
                templates.append(name)
        
        self._template_names = sorted(templates)
        return self._template_names
    
    def clear_cache(self) -> None:
        """Clear all caches to free memory."""
        self._template_cache.clear()
        self._template_gray_cache.clear()
        self._scaled_cache.clear()
        self._template_names = None
