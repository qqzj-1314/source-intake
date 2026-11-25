import importlib.util
import struct
import unittest
import zlib
from pathlib import Path

spec = importlib.util.spec_from_file_location('intake_native', Path(__file__).resolve().parents[1] / 'plugin/native_io.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class ScreenshotConversionTests(unittest.TestCase):
    def test_bottom_up_and_top_down_dib_preserve_pixel_colors(self):
        # Windows rows are BGR with four-byte alignment.
        for height, pixels in [(2, b'\xff\x00\x00\x00\x00\x00\xff\x00'), (-2, b'\x00\x00\xff\x00\xff\x00\x00\x00')]:
            header = struct.pack('<IiiHHIIiiII', 40, 1, height, 1, 24, 0, 8, 0, 0, 0, 0)
            png = native.dib_to_png(header + pixels)
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            offset, data = 8, b''
            while offset < len(png):
                length = struct.unpack_from('>I', png, offset)[0]
                kind = png[offset + 4:offset + 8]
                if kind == b'IDAT':
                    data += png[offset + 8:offset + 8 + length]
                offset += 12 + length
            self.assertEqual(zlib.decompress(data), b'\x00\xff\x00\x00\x00\x00\x00\xff')

    def test_incomplete_and_oversized_dib_are_rejected(self):
        for data in [b'', struct.pack('<IiiHHIIiiII', 40, 100000, 100000, 1, 24, 0, 0, 0, 0, 0, 0), struct.pack('<IiiHHIIiiII', 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0)]:
            with self.assertRaises(ValueError):
                native.dib_to_png(data)
