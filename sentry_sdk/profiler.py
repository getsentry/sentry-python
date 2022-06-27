"""
This file contains code from https://github.com/nylas/nylas-perftools, which is published under the following license:

The MIT License (MIT)

Copyright (c) 2014 Nylas

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

import atexit
import os
import signal
import time
from sentry_sdk.utils import logger

def nanosecond_time():
    return int(time.perf_counter() * 1e9)

class FrameData:
    def __init__(self, frame):
        self._function_name = frame.f_code.co_name
        self._module = frame.f_globals['__name__']

        # Depending on Python version, frame.f_code.co_filename either stores just the file name or the entire absolute path.
        self._file_name = os.path.basename(frame.f_code.co_filename)
        self._abs_path = os.path.abspath(frame.f_code.co_filename) # TODO: Must verify this will give us correct absolute paths in all cases!
        self._line_number = frame.f_lineno

    def __str__(self):
        return f'{self._function_name}({self._module}) in {self._file_name}:{self._line_number}'

class StackSample:
    def __init__(self, top_frame, profiler_start_time):
        self._sample_time = nanosecond_time() - profiler_start_time
        self._stack = []
        self._add_all_frames(top_frame)

    def _add_all_frames(self, top_frame):
        frame = top_frame
        while frame is not None:
            self._stack.append(FrameData(frame))
            frame = frame.f_back

    def __str__(self):
        return f'Time: {self._sample_time}; Stack: {[str(frame) for frame in reversed(self._stack)]}'

class Sampler(object):
    """
    A simple stack sampler for low-overhead CPU profiling: samples the call
    stack every `interval` seconds and keeps track of counts by frame. Because
    this uses signals, it only works on the main thread.
    """
    def __init__(self, interval=0.01):
        self.interval = interval
        self._stack_samples = []
    
    def __enter__(self):
        self.start()
    
    def __exit__(self, *_):
        self.stop()
        print(self)

    def start(self):
        self._start_time = nanosecond_time()
        self._stack_samples = []
        try:
            signal.signal(signal.SIGVTALRM, self._sample)
        except ValueError:
            logger.warn('Profiler failed to run because it was started from a non-main thread') # TODO: Does not print anything
            return 

        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval)
        atexit.register(self.stop)

    def _sample(self, signum, frame):
        self._stack_samples.append(StackSample(frame, self._start_time))
        #print('j')
        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval)

    def _format_frame(self, frame):
        return '{}({})'.format(frame.f_code.co_name,
                               frame.f_globals.get('__name__'))

    def samples(self):
        return len(self._stack_samples)

    def __str__(self):
        return '\n'.join([str(sample) for sample in self._stack_samples])

    def stop(self):
        signal.setitimer(signal.ITIMER_VIRTUAL, 0)

    def __del__(self):
        self.stop()
