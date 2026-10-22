# Copyright (c) Meta Platforms, Inc. and affiliates.
# SPDX-License-Identifier: Apache-2.0
"""Speech gain preserves PCM length and limits peaks without integer overflow."""

import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SpeechGain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / "gain.dylib"
        result = subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "components/muse/muse_tts_audio.c"),
                "-o",
                str(lib),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.muse_tts_boost.argtypes = [
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_float),
        ]

    def boost(self, values, gain=3.0):
        data = (ctypes.c_int16 * len(values))(*values)
        state = ctypes.c_float(gain)
        self.lib.muse_tts_boost(data, len(values), ctypes.byref(state))
        return list(data), state.value

    def test_quiet_speech_gets_nine_db_boost(self):
        out, gain = self.boost([0, 1000, -1000, 4000, -4000])
        self.assertEqual(out, [0, 3000, -3000, 12000, -12000])
        self.assertEqual(gain, 3)

    def test_full_scale_samples_and_min_int_do_not_clip_or_wrap(self):
        out, gain = self.boost([-32768, 32767, 16000, -16000])
        self.assertEqual(len(out), 4)
        self.assertTrue(all(abs(v) <= 30000 for v in out))
        self.assertLess(out[0], 0)
        self.assertGreater(out[1], 0)
        self.assertLess(gain, 1)

    def test_gain_recovers_gradually_after_a_loud_peak(self):
        _, gain = self.boost([32767] * 1200)
        out, recovered = self.boost([1000] * 1200, gain)
        self.assertGreater(recovered, gain)
        self.assertLess(recovered, 3)
        self.assertTrue(all(0 < v < 3000 for v in out))
        self.assertEqual(self.boost([0] * 10)[0], [0] * 10)
