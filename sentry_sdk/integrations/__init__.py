class Integration(object):
    """
    A interface with an install hook for integrations to patch themselves
    into their environment. Integrations don't really *need* to use this hook
    if the idiomatic control flow of some framework mandates otherwise.
    """

    identifier = None

    def install(self, client):
        pass

    @classmethod
    def parse_environment(cls, environ):
        client_options = {}
        integration_options = {}
        for key, value in environ.items():
            if not key.startswith("SENTRY_"):
                continue
            key = key[len("SENTRY_") :].replace("-", "_").lower()
            identifier_prefix = "%s_" % cls.identifier
            if key.startswith(identifier_prefix):
                integration_options[key[len(identifier_prefix) :]] = value
            else:
                client_options[key] = value

        return client_options, integration_options
