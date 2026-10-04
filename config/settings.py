import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

env_file = BASE_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-assessment-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
INSTALLED_APPS = ["django.contrib.contenttypes", "routing"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True, "OPTIONS": {"context_processors": []}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
GEOCODING_API_KEY = os.environ.get("GEOCODING_API_KEY", "")
GEOCODING_BASE_URL = os.environ.get("GEOCODING_BASE_URL", "https://geocode.maps.co").rstrip("/")
GEOCODING_TIMEOUT_SECONDS = float(os.environ.get("GEOCODING_TIMEOUT_SECONDS", "10"))
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://routing.openstreetmap.de/routed-car").rstrip("/")
OSRM_TIMEOUT_SECONDS = float(os.environ.get("OSRM_TIMEOUT_SECONDS", "20"))
OSRM_MIN_REQUEST_INTERVAL_SECONDS = float(os.environ.get("OSRM_MIN_REQUEST_INTERVAL_SECONDS", "1"))
STATION_CORRIDOR_MILES = float(os.environ.get("STATION_CORRIDOR_MILES", "2"))
FUEL_PRICE_POLICY = os.environ.get("FUEL_PRICE_POLICY", "mean")
