from sentry_sdk.integrations import Integration, setup_integrations


class NoOpIntegration(Integration):
    """
    A simple no-op integration for testing purposes.
    """

    identifier = "noop"

    def setup_once():  # type: () -> None
        pass

    def __eq__(self, __value):  # type: (object) -> bool
        """
        All instances of NoOpIntegration should be considered equal to each other.
        """
        return type(__value) == type(self)


def test_multiple_setup_integrations_calls():
    first_call_return = setup_integrations([NoOpIntegration()], with_defaults=False)
    assert first_call_return == {NoOpIntegration.identifier: NoOpIntegration()}

    second_call_return = setup_integrations([NoOpIntegration()], with_defaults=False)
    assert second_call_return == {NoOpIntegration.identifier: NoOpIntegration()}
