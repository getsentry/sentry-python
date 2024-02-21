from django.contrib.auth.models import User
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def postgres_select_orm(request, *args, **kwargs):
    user = User.objects.using("postgres").all().first()
    return HttpResponse("ok {}".format(user))
