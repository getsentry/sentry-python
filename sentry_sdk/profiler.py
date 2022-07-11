"""
This file contains code from https://github.com/nylas/nylas-perftools, which is published under the following license:

The MIT License (MIT)

Copyright (c) 2014 Nylas

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

import atexit
import signal
import time
import threading
import json
from sentry_sdk.utils import logger

# TODO: temp import to output file
import os

def nanosecond_time():
    return int(time.perf_counter() * 1e9)

class FrameData:
    def __init__(self, frame):
        self.function_name = frame.f_code.co_name
        self.module = frame.f_globals['__name__']

        # Depending on Python version, frame.f_code.co_filename either stores just the file name or the entire absolute path.
        self.file_name = frame.f_code.co_filename
        self.line_number = frame.f_code.co_firstlineno
    
    @property
    def _attribute_tuple(self):
        """Returns a tuple of the attributes used in comparison"""
        return (self.function_name, self.module, self.file_name, self.line_number)

    def __eq__(self, other):
        if isinstance(other, FrameData):
            return self._attribute_tuple == other._attribute_tuple
        return False
    
    def __hash__(self):
        return hash(self._attribute_tuple)

    def __str__(self):
        return f'{self.function_name}({self.module}) in {self.file_name}:{self.line_number}'

class StackSample:
    def __init__(self, top_frame, profiler_start_time, frame_indices):
        self.sample_time = nanosecond_time() - profiler_start_time
        self.stack = []
        self._add_all_frames(top_frame, frame_indices)

    def _add_all_frames(self, top_frame, frame_indices):
        frame = top_frame
        while frame is not None:
            frame_data = FrameData(frame)
            if frame_data not in frame_indices:
                frame_indices[frame_data] = len(frame_indices)
            self.stack.append(frame_indices[frame_data])
            frame = frame.f_back
        self.stack = list(reversed(self.stack))

    def __str__(self):
        return f'Time: {self.sample_time}; Stack: {[str(frame) for frame in reversed(self.stack)]}'

class Sampler(object):
    """
    A simple stack sampler for low-overhead CPU profiling: samples the call
    stack every `interval` seconds and keeps track of counts by frame. Because
    this uses signals, it only works on the main thread.
    """
    def __init__(self, transaction, interval=0.01):
        self.interval = interval
        self.stack_samples = []
        self._frame_indices = dict()
        self._transaction = transaction
        transaction._profile = self
    
    def __enter__(self):
        self.start()
    
    def __exit__(self, *_):
        self.stop()

    def start(self):
        self._start_time = nanosecond_time()
        self.stack_samples = []
        self._frame_indices = dict()
        try:
            signal.signal(signal.SIGVTALRM, self._sample)
        except ValueError:
            logger.warn('Profiler failed to run because it was started from a non-main thread') # TODO: Does not print anything
            return 

        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval)
        atexit.register(self.stop)
    
    def sample_weights(self):
        """
        Return the weights of each sample (difference between the sample's and previous sample's timestamp).
        """
        if self.stack_samples == []:
            return []

        return [self.stack_samples[0].sample_time, *(sample.sample_time - prev_sample.sample_time for sample, prev_sample in zip(self.stack_samples[1:], self.stack_samples))]

    def _sample(self, _, frame):
        self.stack_samples.append(StackSample(frame, self._start_time, self._frame_indices))
        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval)

    def to_json(self):
        """
        Exports this object to a JSON format compatible with Sentry's profiling visualizer.
        Returns dictionary which can be serialized to JSON.
        """
        thread_id = threading.get_ident()
        return {
            'samples': [{
                'frames': sample.stack,
                'relative_timestamp_ns': sample.sample_time,
                'thread_id': thread_id
            } for sample in self.stack_samples],
            'frames': [{
                'name': frame.function_name,
                'file': frame.file_name,
                'line': frame.line_number
            } for frame in self.frame_list()]
        }

    def frame_list(self):
         # Build frame array from the frame indices
        frames = [None] * len(self._frame_indices)
        for frame, index in self._frame_indices.items():
            frames[index] = frame
        return frames

    def samples(self):
        return len(self.stack_samples)

    def __str__(self):
        return '\n'.join([str(sample) for sample in self.stack_samples])

    def stop(self):
        signal.setitimer(signal.ITIMER_VIRTUAL, 0)

    def __del__(self):
        self.stop()
    
    @property
    def transaction_name(self):
        return self._transaction.name
