import sentry_sdk
import threading
import time

from flask import Flask
from sentry_sdk.integrations.flask import FlaskIntegration


sentry_sdk.init(
    # dsn="https://d1398009b04549f6aa90bd8a362c9365@o1137848.ingest.sentry.io/6600324",
    dsn="http://60d3409215134fd1a60765f2400b6b38@localhost:3001/1",
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0,
    environment="stuff",
    debug=True,
    _experiments={
        "profiles_sample_rate": 1.0,
    },
)


app = Flask(__name__)


@app.route("/f/<int:n>")
def f_route(n):
    # type: (int) -> str
    return str(fib(n))


def fib(n):
    # type: (int) -> int
    start = time.perf_counter_ns()
    x = _fib(n)
    stop = time.perf_counter_ns()
    thread = threading.current_thread()
    t_name = f"{thread.__class__.__name__}: {thread.name}"
    sentry_sdk.set_tag("thread", t_name)
    sentry_sdk.set_tag("fib", str(n))
    print(f"{t_name} elapsed {(stop - start) / 1e6}ms")
    return x


def _fib(n):
    # type: (int) -> int
    with sentry_sdk.start_span(op="fib", description=str(n)):
        if n < 0:
            raise ValueError("positive numbers only")
        elif n == 0 or n == 1:
            return n
        return _fib(n - 1) + _fib(n - 2)
