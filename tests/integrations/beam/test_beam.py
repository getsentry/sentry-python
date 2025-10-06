import pytest
import inspect

import dill

from sentry_sdk.integrations.beam import (
    BeamIntegration,
    _wrap_task_call,
    _wrap_inspect_call,
)

from apache_beam.typehints.trivial_inference import instance_to_type
from apache_beam.typehints.decorators import getcallargs_forhints
from apache_beam.transforms.core import DoFn, ParDo, _DoFnParam, CallableWrapperDoFn
from apache_beam.runners.common import DoFnInvoker, DoFnContext
from apache_beam.utils.windowed_value import WindowedValue

try:
    from apache_beam.runners.common import OutputHandler
except ImportError:
    from apache_beam.runners.common import OutputProcessor as OutputHandler


def foo():
    return True


def bar(x, y):
    # print(x + y)
    return True


def baz(x, y=2):
    # print(x + y)
    return True


class A:
    def __init__(self, fn):
        self.r = "We are in A"
        self.fn = fn
        self._inspect_fn = _wrap_inspect_call(self, "fn")

    def process(self):
        return self.fn()


class B(A):
    def fa(self, x, element=False, another_element=False):
        if x or (element and not another_element):
            # print(self.r)
            return True
        1 / 0
        return False

    def __init__(self):
        self.r = "We are in B"
        super().__init__(self.fa)


class SimpleFunc(DoFn):
    def process(self, x):
        if x:
            1 / 0
        return [True]


class PlaceHolderFunc(DoFn):
    def process(self, x, timestamp=DoFn.TimestampParam, wx=DoFn.WindowParam):
        if isinstance(timestamp, _DoFnParam) or isinstance(wx, _DoFnParam):
            raise Exception("Bad instance")
        if x:
            1 / 0
        yield True


def fail(x):
    if x:
        1 / 0
    return [True]


test_parent = A(foo)
test_child = B()
test_simple = SimpleFunc()
test_place_holder = PlaceHolderFunc()
test_callable = CallableWrapperDoFn(fail)


# Cannot call simple functions or placeholder test.
@pytest.mark.parametrize(
    "obj,f,args,kwargs",
    [
        [test_parent, "fn", (), {}],
        [test_child, "fn", (False,), {"element": True}],
        [test_child, "fn", (True,), {}],
        [test_simple, "process", (False,), {}],
        [test_callable, "process", (False,), {}],
    ],
)
def test_monkey_patch_call(obj, f, args, kwargs):
    func = getattr(obj, f)

    assert func(*args, **kwargs)
    assert _wrap_task_call(func)(*args, **kwargs)


@pytest.mark.parametrize("f", [foo, bar, baz, test_parent.fn, test_child.fn])
def test_monkey_patch_pickle(f):
    f_temp = _wrap_task_call(f)
    assert dill.pickles(f_temp), "{} is not pickling correctly!".format(f)

    # Pickle everything
    s1 = dill.dumps(f_temp)
    s2 = dill.loads(s1)
    dill.dumps(s2)


@pytest.mark.parametrize(
    "f,args,kwargs",
    [
        [foo, (), {}],
        [bar, (1, 5), {}],
        [baz, (1,), {}],
        [test_parent.fn, (), {}],
        [test_child.fn, (False,), {"element": True}],
        [test_child.fn, (True,), {}],
    ],
)
def test_monkey_patch_signature(f, args, kwargs):
    arg_types = [instance_to_type(v) for v in args]
    kwargs_types = {k: instance_to_type(v) for (k, v) in kwargs.items()}
    f_temp = _wrap_task_call(f)
    try:
        getcallargs_forhints(f, *arg_types, **kwargs_types)
    except Exception:
        print("Failed on {} with parameters {}, {}".format(f, args, kwargs))
        raise
    try:
        getcallargs_forhints(f_temp, *arg_types, **kwargs_types)
    except Exception:
        print("Failed on {} with parameters {}, {}".format(f_temp, args, kwargs))
        raise
    try:
        expected_signature = inspect.signature(f)
        test_signature = inspect.signature(f_temp)
        assert expected_signature == test_signature, (
            "Failed on {}, signature {} does not match {}".format(
                f, expected_signature, test_signature
            )
        )
    except Exception:
        # expected to pass for py2.7
        pass


class _OutputHandler(OutputHandler):
    def process_outputs(
        self, windowed_input_element, results, watermark_estimator=None
    ):
        self.handle_process_outputs(
            windowed_input_element, results, watermark_estimator
        )

    def handle_process_outputs(
        self, windowed_input_element, results, watermark_estimator=None
    ):
        print(windowed_input_element)
        try:
            for result in results:
                assert result
        except StopIteration:
            print("In here")


@pytest.fixture
def init_beam(sentry_init):
    def inner(fn):
        sentry_init(default_integrations=False, integrations=[BeamIntegration()])
        # Little hack to avoid having to run the whole pipeline.
        pardo = ParDo(fn)
        signature = pardo._signature
        output_processor = _OutputHandler()
        return DoFnInvoker.create_invoker(
            signature,
            output_processor,
            DoFnContext("test"),
            input_args=[],
            input_kwargs={},
        )

    return inner


@pytest.mark.parametrize("fn", [test_simple, test_callable, test_place_holder])
def test_invoker_normal(init_beam, fn):
    invoker = init_beam(fn)
    print("Normal testing {} with {} invoker.".format(fn, invoker))
    windowed_value = WindowedValue(False, 0, [None])
    invoker.invoke_process(windowed_value)


@pytest.mark.parametrize("fn", [test_simple, test_callable, test_place_holder])
def test_invoker_exception(init_beam, capture_events, capture_exceptions, fn):
    invoker = init_beam(fn)
    events = capture_events()

    print("Exception testing {} with {} invoker.".format(fn, invoker))
    # Window value will always have one value for the process to run.
    windowed_value = WindowedValue(True, 0, [None])
    try:
        invoker.invoke_process(windowed_value)
    except Exception:
        pass

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "beam"
