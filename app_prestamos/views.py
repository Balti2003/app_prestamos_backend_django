from rest_framework import viewsets, status, filters
from rest_framework.response import Response
from .models import Cliente, Prestamo, Cuota, Caja
from .serializers import ClienteSerializer, PrestamoSerializer, CuotaSerializer, CajaSerializer
from rest_framework.decorators import action
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from .filters import PrestamoFilter, CuotaFilter


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.filter(activo=True)
    serializer_class = ClienteSerializer


class PrestamoViewSet(viewsets.ModelViewSet):
    queryset = Prestamo.objects.filter(activo=True)
    serializer_class = PrestamoSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = PrestamoFilter # Vinculamos el filtro
    search_fields = ['cliente__nombre', 'cliente__apellido', 'cliente__dni'] # Habilitamos búsqueda por nombre de cliente o DNI
    ordering_fields = ['fecha_inicio', 'monto_solicitado'] # Permitimos ordenar por fecha de inicio o monto
    
    # Sobrescribimos el método create para disparar la lógica de cuotas
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Validamos que haya plata en la caja
        monto_solicitado = serializer.validated_data['monto_solicitado']
        saldo_disponible = Caja.saldo_actual()

        if saldo_disponible < monto_solicitado:
            # Si no hay plata, frenamos todo y devolvemos error 400
            return Response(
                {
                    "error": "Fondos insuficientes en caja.",
                    "saldo_actual": float(saldo_disponible),
                    "monto_requerido": float(monto_solicitado)
                }, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Si pasó la validación, guardamos el préstamo
        prestamo = serializer.save()
        
        # Generamos las cuotas
        prestamo.generar_plan_pagos()
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class CuotaViewSet(viewsets.ModelViewSet):
    queryset = Cuota.objects.all()
    serializer_class = CuotaSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = CuotaFilter
    ordering_fields = ['fecha_vencimiento', 'numero_cuota']
    
    @action(detail=True, methods=['post'])
    def registrar_pago(self, request, pk=None):
        cuota = self.get_object()
        
        if cuota.esta_pagada:
            return Response({'error': 'Esta cuota ya fue pagada.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Registramos el pago
        cuota.esta_pagada = True
        cuota.fecha_pago_real = timezone.now()
        
        # Aquí calculamos si hubo mora al momento del pago
        mora = cuota.calcular_mora()
        
        cuota.save()

        # Registrar el movimiento en la caja
        Caja.objects.create(
            tipo='ingreso',
            monto=cuota.monto_total + mora,
            concepto=f"Pago cuota {cuota.numero_cuota} - Préstamo #{cuota.prestamo.id}"
        )

        return Response({
            'status': 'Pago registrado exitosamente',
            'mora_cobrada': mora,
            'total_recibido': cuota.monto_total + mora
        })


class CajaViewSet(viewsets.ModelViewSet):
    queryset = Caja.objects.all().order_by('-fecha') # Los últimos movimientos primero
    serializer_class = CajaSerializer