from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('app_prestamos.urls')), #Prefijo api para todas las rutas
]
