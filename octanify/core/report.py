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
        self.nodes_unsupported: int = 0
        self.links_created: int = 0
        self.links_failed: int = 0
        self.approximations: list[str] = []
        self.notices: list[str] = []
        self.warnings: list[str] = []
        
    def clear(self) -> None:
        """Reset the report data before a new conversion."""
        self.materials_converted = 0
        self.nodes_translated = 0
        self.nodes_unsupported = 0
        self.links_created = 0
        self.links_failed = 0
        self.approximations.clear()
        self.notices.clear()
        self.warnings.clear()
        
    def add_warning(self, message: str) -> None:
        """Add a formatted warning message."""
        if message not in self.warnings:
            self.warnings.append(message)

    def add_approximation(self, message: str) -> None:
        """Record a conversion that is usable but not perfectly equivalent."""
        if message not in self.approximations:
            self.approximations.append(message)

    def add_notice(self, message: str) -> None:
        """Record an informational conversion action handled automatically."""
        if message not in self.notices:
            self.notices.append(message)

    def add_link_failure(self, message: str) -> None:
        """Record a failed link reconstruction."""
        self.links_failed += 1
        self.add_warning(message)

    def add_unsupported(self, message: str) -> None:
        """Record an unsupported node conversion."""
        self.nodes_unsupported += 1
        self.add_warning(message)

    def recover_unsupported(self, short_type: str) -> None:
        """Mark one temporary fallback as recovered by a post-process pass."""
        suffix = f"Unsupported: {short_type}"
        for index, warning in enumerate(self.warnings):
            if warning.endswith(suffix):
                del self.warnings[index]
                self.nodes_unsupported = max(0, self.nodes_unsupported - 1)
                break

# Global singleton instance for the session
report_data = ConversionReport()
