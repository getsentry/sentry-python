_locked = False
_integrations = {}


def register_integration(identifier, integration=None):
    def inner(integration):
        if _locked:
            raise ValueError(
                "A client has already been initialized. "
                "Registration of integrations no longer possible"
            )
        if identifier in _integrations:
            raise ValueError("Integration with that name already exists.")
        _integrations[identifier] = integration

    if integration is not None:
        return inner(integration)
    return inner


def get_integration(identifier):
    global _locked
    _locked = True
    return _integrations[identifier]


@register_integration("django")
def _django_integration(*a, **kw):
    from .django import install

    return install(*a, **kw)


@register_integration("flask")
def _flask_integration(*a, **kw):
    from .flask import install

    return install(*a, **kw)


@register_integration("celery")
def _celery_integration(*a, **kw):
    from .celery import install

    return install(*a, **kw)
