# Integration with Flask

Easiest way to get started:

    from flask import Flask
    from sentry_sdk import capture_exception
    from sentry_sdk.integrations.flask import FlaskSentry

    app = Flask(__name__)

    app.config["SENTRY_DSN"] = "https://foo@sentry.io/123"
    flask_sentry = FlaskSentry(app)  # A normal flask extension

    # The sentry client is set up, capture_* methods are usable anywhere
    capture_exception(ValueError())

    @app.route("/")
    def index():
        capture_exception(ValueError())

        # logging
        app.logger.debug("hi")  # this will be a breadcrumb
        app.logger.debug("oh no")  # this will be an event

        # thrown errors are captured
        1 / 0  

## Configuration

The Flask extension can be configured through ``app.config``. The valid keys
are:

* ``SENTRY_FLASK_SETUP_LOGGER``: Whether the sentry logging integration should
  be set up for ``app.logger``.

Other options starting with ``SENTRY_`` are passed on to ``init`` (such as
``dsn``)

## Manual configuration

If reading the configuration from ``app.config`` is too much magic for you, you
can also use the lower-level API like this:

    from flask import Flask
    from sentry_sdk import init, capture_exception
    from sentry_sdk.integrations.flask import FlaskIntegration


    app = Flask(__name__)
    # app.config["SENTRY_DSN"] = "garbage"  # would be ignored

    sentry_integration = FlaskIntegration(app, setup_logger=True)

    # client config is passed normally through init
    init(dsn="https://foo@sentry.io/123", integrations=[sentry_integration])

    # the routes look the same


In this example, your configuration for Sentry is separate from your app
config, and also the config for the Flask integration is separate from your
general Sentry config.
