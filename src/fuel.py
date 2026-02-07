"""
Fuel reading module - OCR-based fuel detection.
Uses EasyOCR with lazy loading for memory efficiency.
"""
from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Lazy-loaded modules
_ocr_reader = None
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


def _get_ocr():
    """Lazy load EasyOCR reader."""
    global _ocr_reader
    if _ocr_reader is None:
        print("[INFO] Loading OCR model (first time only)...")
        
        # Suppress PyTorch pin_memory warning (no GPU available)
        import warnings
        warnings.filterwarnings("ignore", message=".*pin_memory.*")
        
        import easyocr
        _ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        print("[OK] OCR ready!")
    return _ocr_reader


def unload_ocr() -> None:
    """Unload OCR to free memory."""
    global _ocr_reader
    if _ocr_reader is not None:
        _ocr_reader = None
        print("[INFO] OCR unloaded to save memory")


class FuelReader:
    """
    Reads fuel values from screen using OCR.
    
    Expects fuel format like "75/75" or "75 / 75".
    """
    
    # Image preprocessing constants
    MIN_HEIGHT_FOR_OCR = 60
    UPSCALE_FACTOR = 2.0
    BINARY_THRESHOLD = 180
    
    # Fuel value limits
    MIN_FUEL_VALUE = 0
    MAX_FUEL_VALUE = 200  # Reasonable max for validation
    
    # OCR character substitutions (common misreads)
    OCR_SUBSTITUTIONS = {
        'O': '0', 'o': '0',
        'l': '1', 'I': '1', '|': '1',
        'S': '5', 's': '5',
        'B': '8',
    }
    
    # Pre-compiled regex patterns for performance
    _RE_CLEAN_TEXT = re.compile(r'[^0-9\s/\\:]')
    _RE_STANDARD_FUEL = re.compile(r"(\d{1,3})\s*[/\\]")
    _RE_TIMER_FUEL = re.compile(r"(\d{1,2})\s*[/\\]\s*\d+:\d+")
    _RE_TIMER_PATTERN = re.compile(r'\d+:\d+')
    _RE_NON_DIGIT = re.compile(r'[^0-9\s]')
    
    def __init__(self, max_fuel: int = 150):
        """
        Initialize fuel reader.
        
        Args:
            max_fuel: Maximum expected fuel value for validation
        """
        self._last_fuel: Optional[int] = None
        self._max_fuel = max_fuel
    
    def read(self, image: np.ndarray) -> Optional[int]:
        """
        Read fuel value from an image region.
        
        Args:
            image: BGR image containing fuel display
        
        Returns:
            Current fuel value, or None if unreadable
        """
        np = _get_np()
        
        if image is None or image.size == 0:
            return None
        
        cv2 = _get_cv2()
        
        try:
            reader = _get_ocr()
            
            # Pre-processing for better OCR
            gray = self._preprocess_image(image)
            
            # Apply binary threshold to isolate white text
            _, binary = cv2.threshold(gray, self.BINARY_THRESHOLD, 255, cv2.THRESH_BINARY)
            
            # OCR the image (try binary first, then gray if no results)
            results = reader.readtext(binary, detail=0, paragraph=False)
            if not results:
                results = reader.readtext(gray, detail=0, paragraph=False)
            
            if not results:
                return None
            
            # Join all detected text
            text = " ".join(results)
            
            # Parse fuel format: "75/75" or "75 / 75" etc.
            fuel = self._parse_fuel_text(text)
            
            if fuel is not None:
                self._last_fuel = fuel
            else:
                print(f"[DEBUG] OCR/Parse failed. Raw text: '{text}'")
            
            return fuel
            
        except (ValueError, RuntimeError, ImportError) as e:
            print(f"[WARNING] OCR failed: {e}")
            return None
    
    def _preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """Preprocess image for better OCR recognition."""
        np = _get_np()
        cv2 = _get_cv2()
        
        # Convert to grayscale using Max channel (better for colored/red text)
        if len(image.shape) == 3:
            # Use max of BGR channels to make Red/Yellow text bright
            # Standard BGR2GRAY makes red dark, which fails thresholding
            gray = np.max(image, axis=2)
        else:
            gray = image
        
        # Resize (upscale) to help with small text
        # Standard height for OCR is ~30-60px, smaller text fails
        h, w = gray.shape[:2]
        if h < self.MIN_HEIGHT_FOR_OCR:
            scale = self.MIN_HEIGHT_FOR_OCR / h
            new_w = int(w * scale)
            gray = cv2.resize(gray, (new_w, self.MIN_HEIGHT_FOR_OCR), interpolation=cv2.INTER_CUBIC)
        else:
            # Always 2x upscale for better accuracy
            gray = cv2.resize(
                gray, None, fx=self.UPSCALE_FACTOR, fy=self.UPSCALE_FACTOR,
                interpolation=cv2.INTER_CUBIC
            )
        
        return gray
    
    def _parse_fuel_text(self, text: str) -> Optional[int]:
        """
        Parse fuel value from OCR text.
        
        Handles multiple formats:
        - Normal: "75/75" or "75 / 75"  
        - Low fuel with timer: "1/ 3:00" or "4/ 2:15"
        - Partial reads: "/75" (when crop missed the number)
        """
        original_text = text
        
        # Apply common OCR substitutions
        for wrong, correct in self.OCR_SUBSTITUTIONS.items():
            text = text.replace(wrong, correct)
        
        # Keep colons to detect timer patterns, remove other special chars
        text_for_analysis = self._RE_CLEAN_TEXT.sub('', text)
        
        # Pattern 1: Standard fuel format "XX/YY" - number before slash
        match = self._RE_STANDARD_FUEL.search(text_for_analysis)
        if match:
            try:
                current = int(match.group(1))
                if self.MIN_FUEL_VALUE <= current <= self.MAX_FUEL_VALUE:
                    return current
            except ValueError:
                pass
        
        # Pattern 2: Low fuel with timer - look for single digit before slash+colon pattern
        # Format: "X/ Y:ZZ" where X is fuel, Y:ZZ is timer
        # OCR might read this as "4 / 2:15" or "1/ 3:00"
        timer_match = self._RE_TIMER_FUEL.search(text_for_analysis)
        if timer_match:
            try:
                current = int(timer_match.group(1))
                if self.MIN_FUEL_VALUE <= current <= self._max_fuel:
                    return current
            except ValueError:
                pass
        
        # Pattern 3: If we see a colon (timer), look for any leading digit that's NOT part of timer
        # Timer format is typically X:XX (minutes:seconds)
        if ':' in text_for_analysis:
            # Find all numbers in the text
            all_numbers = re.findall(r'\d+', text_for_analysis)
            
            # The first short number (1-2 digits, not part of time) is likely fuel
            for num_str in all_numbers:
                try:
                    num = int(num_str)
                    # Skip if it looks like timer minutes/seconds (usually < 60)
                    # But fuel 1-4 also fits this... 
                    # Key insight: fuel appears BEFORE the slash
                    if self.MIN_FUEL_VALUE <= num <= self._max_fuel:
                        # Check if this number appears before a slash
                        if re.search(rf'{num}\s*[/\\]', text_for_analysis):
                            return num
                except ValueError:
                    pass
        
        # Pattern 4: Fallback - any standalone number that's reasonable for fuel
        # Remove colon-based patterns first (timer digits)
        text_no_timer = self._RE_TIMER_PATTERN.sub('', text_for_analysis)
        text_no_timer = self._RE_NON_DIGIT.sub('', text_no_timer)
        
        parts = text_no_timer.strip().split()
        if parts:
            try:
                first_val = int(parts[0])
                if self.MIN_FUEL_VALUE <= first_val <= self._max_fuel:
                    return first_val
            except ValueError:
                pass

        return None
    
    def get_last_fuel(self) -> Optional[int]:
        """Get the last successfully read fuel value."""
        return self._last_fuel
