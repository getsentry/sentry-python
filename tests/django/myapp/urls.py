"""myapp URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from __future__ import absolute_import

try:
    from django.urls import path
except ImportError:
    from django.conf.urls import url as path

from . import views

urlpatterns = [
    path('self-check', views.self_check, name='self_check'),
    path('view-exc', views.view_exc, name='view_exc'),
    path('middleware-exc', views.self_check, name='middleware_exc'),
    path('get-dsn', views.get_dsn, name='get_dsn')
]
