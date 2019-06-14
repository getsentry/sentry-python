To run this app:

1. Have a Redis on the Redis default port (if you have Sentry running locally,
   you probably already have this)
2. `pip install sentry-sdk flask rq`
3. `FLASK_APP=tracing flask run`
4. `FLASK_APP=tracing flask worker`
5. Go to `http://localhost:5000/` and enter a base64-encoded string (one is prefilled)
6. Hit submit, wait for heavy computation to end
7. `cat events | python traceviewer.py | dot -T svg > events.svg`
8. `open events.svg`

The last two steps are for viewing the traces. Nothing gets sent to Sentry
right now because Sentry does not deal with this data yet.
