from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.response import Response
from .models import Cliente, Prestamo, Cuota, Caja
from .serializers import ClienteSerializer, PrestamoSerializer, CuotaSerializer
from rest_framework.decorators import action
from django.utils import timezone


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer

class PrestamoViewSet(viewsets.ModelViewSet):
    queryset = Prestamo.objects.all()
    serializer_class = PrestamoSerializer

    def create(self, request, *args, **kwargs):
        # Sobrescribimos el método create para disparar la lógica de cuotas
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Guardamos el préstamo
        prestamo = serializer.save()
        
        # Disparamos la función que definimos en el modelo
        prestamo.generar_plan_pagos()
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

class CuotaViewSet(viewsets.ModelViewSet):
    queryset = Cuota.objects.all()
    serializer_class = CuotaSerializer
    
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