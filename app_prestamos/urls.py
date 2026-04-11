from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClienteViewSet, PrestamoViewSet, CuotaViewSet
from .dashboard_views import DashboardResumenView

router = DefaultRouter()
router.register(r'clientes', ClienteViewSet)
router.register(r'prestamos', PrestamoViewSet)
router.register(r'cuotas', CuotaViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/resumen/', DashboardResumenView.as_view(), name='dashboard-resumen'),
]