from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .dashboard_views import DashboardResumenView
from .views import (
    CajaDiariaViewSet,
    CajaViewSet,
    CambiarPasswordView,
    ClienteViewSet,
    CuotaViewSet,
    GarantiaClienteViewSet,
    PrestamoViewSet,
)

router = DefaultRouter()
router.register(r'clientes', ClienteViewSet)
router.register(r'garantias', GarantiaClienteViewSet)
router.register(r'prestamos', PrestamoViewSet)
router.register(r'cuotas', CuotaViewSet)
# 1. Movimientos o transacciones individuales (Ingresos / Egresos)
router.register(r'caja', CajaViewSet)
# 2. Aperturas, cierres y arqueos de la jornada
router.register(r'caja-diaria', CajaDiariaViewSet, basename='caja-diaria')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/resumen/', DashboardResumenView.as_view(), name='dashboard-resumen'),
    path('usuario/cambiar-password/', CambiarPasswordView.as_view(), name='cambiar-password'),
]