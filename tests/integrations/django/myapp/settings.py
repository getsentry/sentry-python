"""
Django settings for myapp project.

Generated by 'django-admin startproject' using Django 2.0.7.

For more information on this file, see
https://docs.djangoproject.com/en/2.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/2.0/ref/settings/
"""


# We shouldn't access settings while setting up integrations. Initialize SDK
# here to provoke any errors that might occur.
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

sentry_sdk.init(integrations=[DjangoIntegration()])


import os

try:
    # Django >= 1.10
    from django.utils.deprecation import MiddlewareMixin
except ImportError:
    # Not required for Django <= 1.9, see:
    # https://docs.djangoproject.com/en/1.10/topics/http/middleware/#upgrading-pre-django-1-10-style-middleware
    MiddlewareMixin = object

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/2.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "u95e#xr$t3!vdux)fj11!*q*^w^^r#kiyrvt3kjui-t_k%m3op"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["localhost"]


# Application definition

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tests.integrations.django.myapp",
]


class TestMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # https://github.com/getsentry/sentry-python/issues/837 -- We should
        # not touch the resolver_match because apparently people rely on it.
        if request.resolver_match:
            assert not getattr(request.resolver_match.callback, "__wrapped__", None)

        if "middleware-exc" in request.path:
            1 / 0

    def process_response(self, request, response):
        return response


def TestFunctionMiddleware(get_response):  # noqa: N802
    def middleware(request):
        return get_response(request)

    return middleware


MIDDLEWARE_CLASSES = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "tests.integrations.django.myapp.settings.TestMiddleware",
]

if MiddlewareMixin is not object:
    MIDDLEWARE = MIDDLEWARE_CLASSES + [
        "tests.integrations.django.myapp.settings.TestFunctionMiddleware"
    ]


ROOT_URLCONF = "tests.integrations.django.myapp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "debug": True,
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "tests.integrations.django.myapp.wsgi.application"


# Database
# https://docs.djangoproject.com/en/2.0/ref/settings/#databases

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

try:
    import psycopg2  # noqa

    DATABASES["postgres"] = {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": os.environ["SENTRY_PYTHON_TEST_POSTGRES_NAME"],
        "USER": os.environ["SENTRY_PYTHON_TEST_POSTGRES_USER"],
        "PASSWORD": os.environ["SENTRY_PYTHON_TEST_POSTGRES_PASSWORD"],
        "HOST": os.environ.get("SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"),
        "PORT": 5432,
    }
except (ImportError, KeyError):
    pass


# Password validation
# https://docs.djangoproject.com/en/2.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# https://docs.djangoproject.com/en/2.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_L10N = True

USE_TZ = False

TEMPLATE_DEBUG = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.0/howto/static-files/

STATIC_URL = "/static/"

# django-channels specific
ASGI_APPLICATION = "tests.integrations.django.myapp.routing.application"
