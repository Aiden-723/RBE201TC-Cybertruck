"""Incremental ASCII raw-count framing; counts are never motion evidence."""
import re


class FrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        frames = []
        for value in data:
            if value == ord('$'):
                self.buffer.clear()
            if not self.buffer and value != ord('$'):
                continue
            self.buffer.append(value)
            if len(self.buffer) > 128:
                self.buffer.clear()
                continue
            if value == ord('#'):
                match = re.fullmatch(rb'\$MAll:([+-]?[0-9]+),([+-]?[0-9]+),([+-]?[0-9]+),([+-]?[0-9]+)#', self.buffer)
                if match:
                    counts = tuple(int(x) for x in match.groups())
                    if all(-2147483648 <= x <= 2147483647 for x in counts):
                        frames.append(counts)
                self.buffer.clear()
        return frames
