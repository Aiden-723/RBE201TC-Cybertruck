"""Pure two-state-transition gate adapted from Pololu's MazeSolver turn().

Source commit: 48c47e4ffaa5cb3d9d0fe35ef3e77ac8786e9de7
https://github.com/pololu/zumo-shield-arduino-library/blob/48c47e4ffaa5cb3d9d0fe35ef3e77ac8786e9de7/examples/MazeSolver/MazeSolver.ino
Original core: count += ABOVE_LINE(sensor) ^ last_status; last_status = ABOVE_LINE(sensor).
Python adaptation: actual entry status, strict bool inputs, saturated count=2.
No GPIO, motor, clock, direction inference, debounce, or angular guarantee.
Caller owns freshness/debounce and the meaning of the observed bool.

The original source is distributed under the following complete MIT notice:
https://github.com/pololu/zumo-shield-arduino-library/blob/48c47e4ffaa5cb3d9d0fe35ef3e77ac8786e9de7/LICENSE.txt

Copyright (c) 2012-2020 Pololu Corporation (www.pololu.com)

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


class LineTransitionGate:
    """Count two observed boolean changes; repeated equal values do not count."""

    def __init__(self, initial_status):
        if type(initial_status) is not bool:
            raise ValueError('invalid_line_status')
        self._last_status = initial_status
        self._count = 0

    @property
    def last_status(self):
        return self._last_status

    @property
    def count(self):
        return self._count

    @property
    def complete(self):
        return self._count == 2

    def observe(self, status):
        if type(status) is not bool:
            raise ValueError('invalid_line_status')
        self._count = min(2, self._count + int(status ^ self._last_status))
        self._last_status = status
        return self.complete
