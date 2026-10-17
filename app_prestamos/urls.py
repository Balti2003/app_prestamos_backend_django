from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .dashboard_views import DashboardResumenView
from .serializers import CustomTokenObtainPairSerializer
from .views import (
    CajaDiariaViewSet,
    CajaViewSet,
    CambiarPasswordView,
    ClienteViewSet,
    CrearOperadorView,
    CuotaViewSet,
    GarantiaClienteViewSet,
    MercadoPagoWebhookView,
    PrestamoViewSet,
    RegistroEmpresaView,
    usuario_actual,
)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

router = DefaultRouter()
router.register(r'clientes', ClienteViewSet)
router.register(r'garantias', GarantiaClienteViewSet)
router.register(r'prestamos', PrestamoViewSet)
router.register(r'cuotas', CuotaViewSet)
router.register(r'caja', CajaViewSet)
router.register(r'caja-diaria', CajaDiariaViewSet, basename='caja-diaria')

urlpatterns = [
    # Ruta de Login interceptada con CustomTokenObtainPairView
    path('token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    path('', include(router.urls)),
    path('me/', usuario_actual, name='usuario_actual'),
    path('dashboard/resumen/', DashboardResumenView.as_view(), name='dashboard-resumen'),
    path('usuario/cambiar-password/', CambiarPasswordView.as_view(), name='cambiar-password'),
    path('crear-operador/', CrearOperadorView.as_view(), name='crear_operador'),
    path('registro-empresa/', RegistroEmpresaView.as_view(), name='registro_empresa'),
    path('webhooks/mercadopago/', MercadoPagoWebhookView.as_view(), name='webhook_mercadopago'),
]