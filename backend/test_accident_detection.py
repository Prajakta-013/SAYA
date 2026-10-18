"""
Tests for the prototype OpenCV accident detector.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ai.accident_detector import (  # noqa: E402
    build_detection_event,
    detect_accident_from_frame_pair,
    simulate_cctv_accident,
)


class TestAccidentDetection(unittest.TestCase):

    def test_simulate_cctv_accident_returns_required_fields(self):
        event = simulate_cctv_accident(camera_id="CAM-MUM-01")

        self.assertTrue(event["accident_detected"])
        for field in (
            "accident_detected",
            "confidence",
            "camera_id",
            "location",
            "latitude",
            "longitude",
            "incident_type",
            "severity",
            "number_of_patients",
        ):
            self.assertIn(field, event)

        self.assertEqual(event["camera_id"], "CAM-MUM-01")
        self.assertGreater(event["confidence"], 0)
        self.assertEqual(event["incident_type"], "Road Accident")
        self.assertEqual(event["severity"], 5)

    def test_build_detection_event_negative(self):
        event = build_detection_event(
            accident_detected=False,
            confidence=0.0,
            camera_id="CAM-MUM-02",
        )
        self.assertFalse(event["accident_detected"])
        self.assertEqual(event["camera_id"], "CAM-MUM-02")
        self.assertIn("Sion", event["location"])

    def test_frame_pair_sudden_change_detects_accident(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")

        # Dark then bright frame ≈ sudden scene change (prototype signal).
        frame_a = np.zeros((120, 160, 3), dtype=np.uint8)
        frame_b = np.full((120, 160, 3), 255, dtype=np.uint8)

        event = detect_accident_from_frame_pair(
            frame_a,
            frame_b,
            camera_id="CAM-MUM-03",
            motion_threshold=10.0,
        )

        if event.get("error") == "OpenCV (cv2) is not installed":
            self.skipTest("OpenCV not installed")

        self.assertTrue(event["accident_detected"])
        self.assertGreater(event["confidence"], 0)
        self.assertEqual(event["camera_id"], "CAM-MUM-03")

    def test_frame_pair_no_change_no_accident(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")

        frame = np.full((120, 160, 3), 40, dtype=np.uint8)
        event = detect_accident_from_frame_pair(
            frame,
            frame.copy(),
            camera_id="CAM-MUM-01",
            motion_threshold=28.0,
        )

        if event.get("error") == "OpenCV (cv2) is not installed":
            self.skipTest("OpenCV not installed")

        self.assertFalse(event["accident_detected"])


if __name__ == "__main__":
    unittest.main()
