"""Explicitly non-localizing development implementation. Never claims a match."""

from .protocol import Location


class Localizer:
    def update(self, frame_rgb, detections, heading_deg) -> Location:
        return Location(None, None, "UNCERTAIN", 0.0, 0, None, 0.0, None)

    def add_reference(self, frame_rgb) -> bool:
        return False
