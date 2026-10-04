from django.urls import path

from routing import views

urlpatterns = [
    path("api/v1/health/", views.health, name="health"),
    path("api/v1/routes/optimize-fuel/", views.optimize_fuel, name="optimize-fuel"),
    path("api/v1/routes/<uuid:trip_id>/map/", views.trip_map, name="trip-map"),
]
