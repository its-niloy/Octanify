"""Octanify — Conversion Report System.

Tracks statistics and warnings during the conversion process
to display a summary to the user in the UI.
"""

from __future__ import annotations

class ConversionReport:
    """Stores data about the most recent conversion process."""
    def __init__(self) -> None:
        self.materials_converted: int = 0
        self.nodes_translated: int = 0
        self.warnings: list[str] = []
        
    def clear(self) -> None:
        """Reset the report data before a new conversion."""
        self.materials_converted = 0
        self.nodes_translated = 0
        self.warnings.clear()
        
    def add_warning(self, message: str) -> None:
        """Add a formatted warning message."""
        if message not in self.warnings:
            self.warnings.append(message)

# Global singleton instance for the session
report_data = ConversionReport()
