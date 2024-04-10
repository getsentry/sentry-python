from django.core import signals
from django.dispatch import receiver

myapp_custom_signal = signals.Signal()
myapp_custom_signal_silenced = signals.Signal()


@receiver(myapp_custom_signal)
def signal_handler(sender, **kwargs):
    assert sender == "hello"


@receiver(myapp_custom_signal_silenced)
def signal_handler_silenced(sender, **kwargs):
    assert sender == "hello"
