import django

if django.VERSION >= (3, 1):
    from django.utils.decorators import sync_and_async_middleware

    from sentry_sdk.integrations.django.asgi import (
        iscoroutinefunction,
        markcoroutinefunction,
    )

    @sync_and_async_middleware
    def simple_middleware(get_response):
        if iscoroutinefunction(get_response):

            async def middleware(request):
                response = await get_response(request)
                return response

        else:

            def middleware(request):
                response = get_response(request)
                return response

        return middleware

    class AsyncProcessViewMiddleware:
        """A correctly-written async-only middleware exposing an async ``process_view``.
        see: https://docs.djangoproject.com/en/5.2/topics/http/middleware/#asynchronous-support

        This is the supported way to write async middleware per the Django docs:
        declare ``async_capable`` and mark the instance as a coroutine when the
        inner ``get_response`` is async.
        """

        async_capable = True
        sync_capable = False

        def __init__(self, get_response):
            self.get_response = get_response
            if iscoroutinefunction(self.get_response):
                markcoroutinefunction(self)

        async def __call__(self, request):
            return await self.get_response(request)

        async def process_view(self, request, view_func, view_args, view_kwargs):
            return None

    class AsyncProcessExceptionMiddleware:
        """Async-only middleware exposing an async ``process_exception`` hook.

        The view raises, so Django invokes ``process_exception``. If the async
        ``process_exception`` is awaited correctly, it returns an ``HttpResponse``
        that short-circuits the error and the request succeeds with status 200.
        """

        async_capable = True
        sync_capable = False

        def __init__(self, get_response):
            self.get_response = get_response
            if iscoroutinefunction(self.get_response):
                markcoroutinefunction(self)

        async def __call__(self, request):
            return await self.get_response(request)

        async def process_exception(self, request, exception):
            from django.http import HttpResponse

            return HttpResponse("handled by async process_exception", status=200)


def custom_urlconf_middleware(get_response):
    def middleware(request):
        request.urlconf = "tests.integrations.django.myapp.custom_urls"
        response = get_response(request)
        return response

    return middleware
