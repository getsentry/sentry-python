# All client configuration options

- ``with_locals`` (default ``True``): Capture frame locals with each error.
- ``max_breadcrumbs`` (default ``100``): Attach the last ``n`` recorded
  breadcrumbs to an event/error.
- ``release`` (default ``None``): The release name for all events, e.g.
  ``"1.0.3"``. This will map up into a release into Sentry.
- ``environment`` (default ``None``): The environment your application is
  running in, e.g.  ``"staging"`` or ``"production"``.
- ``server_name`` (default to the system's hostname): The actual hostname of your
  system.
- ``drain_timeout`` (default ``2.0``): When calling ``Client.close()`` or when
  exiting the contextmanager provided by ``sentry_sdk.init``, wait this many
  seconds for the last events to be sent.
- ``integrations`` (default: ``[]``): Integrations to be enabled in addition to
  the default ``logging`` one. See the other pages for exact documentation for
  each integration.
- ``default_integrations`` (default: ``True``): Whether to enable the default
  integrations (logging being the only one). Note that setting this to
  ``False`` is not the same as passing an empty sequence to ``integrations``,
  which would not disable the logging integration.
- ``repos`` (default: ``{}``): [Repository
  references](https://docs.sentry.io/clientdev/interfaces/repos/). Example:

    'raven': {
        # the name of the repository as registered in Sentry
        'name': 'getsentry/raven-python',
        # the prefix where the local source code is found in the repo
        'prefix': 'src'
    }
- ``dist`` (default: ``None``): An optional distribution identifier (FIXME)
- ``transport`` (default: ``None``): You may pass your own instance of
  ``sentry_sdk.transport.Transport`` if you have special requirements w.r.t.
  how your HTTP data is sent. This interface is not documented yet.
- ``sample_rate`` (default: ``1.0``): Set this to a float value between ``0.0``
  and ``1.0`` to send only that percentage of events (using random sampling).
  The default value ``1.0`` will send all events. ``0.0`` effectively disables
  the client.
- ``send_default_pii`` (default: ``False``): By default Sentry-Python removes
  personally-identifiable information it knows about from events before sending
  them to Sentry. This includes cookie data, authentication headers and
  usernames.
- ``http_proxy`` and ``https_proxy`` (default: from environment): Set these two
  variables if you need Sentry-Python to use a proxy to talk to Sentry. The
  default value uses [``urllib.getproxies()`` from the
  stdlib](https://docs.python.org/3.7/library/urllib.request.html#urllib.request.getproxies).
- ``ignore_errors`` (default: ``[]``): A list of exception classes to ignore.
  The provided list must contain actual classes, "string identifiers" are not
  supported. Subclass hierarchies are respected, for example, setting
  ``ignore_errors=[BaseException]`` will ignore all exceptions.
