import django

if django.VERSION >= (3, 1):
    import asyncio
    from django.utils.decorators import sync_and_async_middleware

    @sync_and_async_middleware
    def simple_middleware(get_response):
        if asyncio.iscoroutinefunction(get_response):

            async def middleware(request):
                response = await get_response(request)
                return response

        else:

            def middleware(request):
                response = get_response(request)
                return response

        return middleware


def custom_urlconf_middleware(get_response):
    def middleware(request):
        request.urlconf = "tests.integrations.django.myapp.custom_urls"
        response = get_response(request)
        return response

    return middleware
