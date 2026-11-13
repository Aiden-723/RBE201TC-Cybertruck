import importlib.util
import unittest


class ParserTests(unittest.TestCase):
    def test_bounded_invalid_and_range(self):
        p = self.parser()
        self.assertEqual(p.feed(b'x' * 100000), [])
        self.assertLessEqual(len(p.buffer), 128)
        for frame in (b'$MAll:2147483648,0,0,0#', b'$MAll:-2147483649,0,0,0#',
                      b'$MAll:1, 2,3,4#', b'$MAll:1,2,3#', b'$MAll:1.0,2,3,4#',
                      b'$MAll:\xff,2,3,4#', b'$MAll:1,2,3,4,5#'):
            self.assertEqual(p.feed(frame), [])
        self.assertEqual(p.feed(b'junk$bad$MAll:-2147483648,2147483647,0,0#'),
                         [(-2147483648, 2147483647, 0, 0)])

    def parser(self):
        self.assertIsNotNone(importlib.util.find_spec('parser'), 'parser module missing')
        from parser import FrameParser
        return FrameParser()

    def test_incremental_signed_counts(self):
        p = self.parser()
        self.assertEqual(p.feed(b'noise$MAll:2,-2,'), [])
        self.assertEqual(p.feed(b'-191,22#$MAll:+1,0,3,-4#'), [(2, -2, -191, 22), (1, 0, 3, -4)])
