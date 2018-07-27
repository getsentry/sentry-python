# Using Sentry with Flask

    from sentry_sdk.integrations.flask import FlaskIntegration
    from sentry_sdk import init

    init(dsn="https://foo@sentry.io/123", integrations=[FlaskIntegration()])

    app = Flask(__name__)


* You can actually run that `init` anywhere. Before or after you define your
  routes, before or after you register extensions.

* The Flask integration will be installed for all of your apps. It hooks into
  Flask's signals, not anything on the app object.

* Request data is attached to all events.

* All exceptions leading to a Internal Server Error are reported.

* Logging with `app.logger` or really *any* logger will create breadcrumbs. See
  logging docs for more information.
