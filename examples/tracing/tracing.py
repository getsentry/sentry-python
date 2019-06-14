import json
import flask
import os
import redis
import rq
import sentry_sdk
import time
import urllib3

from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.rq import RqIntegration


app = flask.Flask(__name__)
redis_conn = redis.Redis()
http = urllib3.PoolManager()
queue = rq.Queue(connection=redis_conn)


def write_event(event):
    with open("events", "a") as f:
        f.write(json.dumps(event))
        f.write("\n")


sentry_sdk.init(
    integrations=[FlaskIntegration(), RqIntegration()],
    traces_sample_rate=1.0,
    debug=True,
    transport=write_event,
)


def decode_base64(encoded, redis_key):
    time.sleep(1)
    r = http.request("GET", "http://httpbin.org/base64/{}".format(encoded))
    redis_conn.set(redis_key, r.data)


@app.route("/")
def index():
    with sentry_sdk.configure_scope() as scope:
        return flask.render_template(
            "index.html",
            sentry_dsn=os.environ["SENTRY_DSN"],
            traceparent=dict(sentry_sdk.Hub.current.iter_trace_propagation_headers()),
        )


@app.route("/compute/<input>")
def compute(input):
    redis_key = "sentry-python-tracing-example-result:{}".format(input)
    redis_conn.delete(redis_key)
    queue.enqueue(decode_base64, encoded=input, redis_key=redis_key)

    return redis_key


@app.route("/wait/<redis_key>")
def wait(redis_key):
    result = redis_conn.get(redis_key)
    if result is None:
        return "NONE"
    else:
        redis_conn.delete(redis_key)
        return "RESULT: {}".format(result)


@app.cli.command("worker")
def run_worker():
    print("WORKING")
    worker = rq.Worker([queue], connection=queue.connection)
    worker.work()
