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
import threading
import json
from sentry_sdk.utils import logger

def nanosecond_time():
    return int(time.perf_counter() * 1e9)

class FrameData:
    def __init__(self, frame):
        self.function_name = frame.f_code.co_name
        self.module = frame.f_globals['__name__']

        # Depending on Python version, frame.f_code.co_filename either stores just the file name or the entire absolute path.
        self.file_name = os.path.basename(frame.f_code.co_filename)
        self.abs_path = os.path.abspath(frame.f_code.co_filename) # TODO: Must verify this will give us correct absolute paths in all cases!
        self.line_number = frame.f_code.co_firstlineno
    
    @property
    def _attribute_tuple(self):
        """Returns a tuple of the attributes used in comparison"""

        # Do not need to include self.file_name because it depends on self.abs_path
        return (self.function_name, self.module, self.abs_path, self.line_number)

    def __eq__(self, other):
        if isinstance(other, FrameData):
            return self._attribute_tuple == other._attribute_tuple
        return False
    
    def __hash__(self):
        return hash(self._attribute_tuple)

    def __str__(self):
        return f'{self.function_name}({self.module}) in {self.file_name}:{self.line_number}'

class StackSample:
    def __init__(self, top_frame, profiler_start_time):
        self.sample_time = nanosecond_time() - profiler_start_time
        self._stack = []
        self._add_all_frames(top_frame)

    def _add_all_frames(self, top_frame):
        frame = top_frame
        while frame is not None:
            self._stack.append(FrameData(frame))
            frame = frame.f_back
        self._stack = list(reversed(self._stack))
    
    def numeric_representation(self, frame_indices):
        """
        Returns a list of numbers representing this stack. The numbers correspond to the value stored in frame_indices
        corresponding to the frame. If a given frame does not exist in frame_indices, it will be added with a value equal
        to the current size of the frame_indices dictionary.
        """
        representation = []
        for frame in self._stack:
            if frame not in frame_indices:
                frame_indices[frame] = len(frame_indices)
            representation.append(frame_indices[frame])
        
        return representation

    def __str__(self):
        return f'Time: {self.sample_time}; Stack: {[str(frame) for frame in reversed(self._stack)]}'

class Sampler(object):
    """
    A simple stack sampler for low-overhead CPU profiling: samples the call
    stack every `interval` seconds and keeps track of counts by frame. Because
    this uses signals, it only works on the main thread.
    """
    def __init__(self, transaction, interval=0.01):
        self.interval = interval
        self.stack_samples = []
        self._transaction = transaction
    
    def __enter__(self):
        self.start()
    
    def __exit__(self, *_):
        self.stop()
        print(self)
        if len(self.stack_samples) > 0:
            with open('test_profile.json', 'w') as f:
                f.write(self.to_json())

    def start(self):
        self._start_time = nanosecond_time()
        self.stack_samples = []
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
        self.stack_samples.append(StackSample(frame, self._start_time))
        #print('j')
        signal.setitimer(signal.ITIMER_VIRTUAL, self.interval)

    def to_json(self):
        """
        Exports this object to a JSON format compatible with Sentry's profiling visualizer
        """
        return json.dumps(self, cls=self.JSONEncoder)
    
    def samples_numeric_representation(self):
        """
        Returns the samples with a numeric representation. There are two return values.
        The first return value actually contains the samples. It is a list of lists of numbers.
        Each list of numbers in the list represents a stack sample. The numbers correspond
        to a frame; specifically, the numbers are indices of the list returned as the second 
        return value. This second return value contains a list of each unique frame that was
        observed. The samples array (first return value) is interpreted by looking up the
        indexes in the frames array (second return value).
        """
        frame_indices = dict() # Stores eventual index of each frame in the frame array
        numeric_representation = [sample.numeric_representation(frame_indices) for sample in self.stack_samples]

        # Build frame array from the frame indices
        frames = [None] * len(frame_indices)
        for frame, index in frame_indices.items():
            frames[index] = frame
        
        return numeric_representation, frames


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
    
    class JSONEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, Sampler):
                samples, frames = o.samples_numeric_representation()
                return {
                    'transactionName': o.transaction_name,
                    'profiles': [{
                        'weights': o.sample_weights(),
                        'samples': samples,
                        'type': 'sampled',
                        'endValue': o.stack_samples[-1].sample_time, # end ts
                        'startValue': 0, # start ts
                        'name': 'main',
                        'unit': 'nanoseconds',
                        'threadID': threading.get_ident()
                    }],
                    'shared': {
                        'frames': [{
                            'name': frame.function_name,
                            'file': frame.abs_path,
                            'line': frame.line_number
                        } for frame in frames] # TODO: Add all elements
                        # 'frames': [{
                        #     'key': string | number,
                        #     'name': string,
                        #     'file': string,
                        #     'line': number,
                        #     'column': number,
                        #     'is_application': boolean,
                        #     'image': string,
                        #     'resource': string,
                        #     'threadId': number
                        # }] # TODO: Add all elements
                    }
                }
            
            else:
                return json.JSONEncoder.default(self, o)
