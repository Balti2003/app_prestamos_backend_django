from rest_framework import viewsets, status, filters
from rest_framework.response import Response
from .models import Cliente, Prestamo, Cuota, Caja
from .serializers import ClienteSerializer, PrestamoSerializer, CuotaSerializer, CajaSerializer
from rest_framework.decorators import action
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from .filters import PrestamoFilter, CuotaFilter
from django.db import transaction
from reportlab.pdfgen import canvas
from django.http import HttpResponse
from io import BytesIO


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
        cuota_actual = self.get_object()
        
        # 1. Validar que no esté ya pagada
        if cuota_actual.esta_pagada:
            return Response(
                {'error': 'Esta cuota ya fue pagada anteriormente.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buscamos si existe alguna cuota del mismo préstamo con un número menor que NO esté pagada
        cuotas_anteriores_pendientes = Cuota.objects.filter(
            prestamo=cuota_actual.prestamo,
            numero_cuota__lt=cuota_actual.numero_cuota,
            esta_pagada=False
        ).exists()

        if cuotas_anteriores_pendientes:
            return Response(
                {
                    'error': 'No se puede cobrar esta cuota. El cliente debe pagar las cuotas anteriores primero.',
                    'detalle': f'Existen cuotas previas a la #{cuota_actual.numero_cuota} pendientes de pago.'
                }, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                # Marcamos como pagada
                cuota_actual.esta_pagada = True
                cuota_actual.fecha_pago_real = timezone.now().date()
                
                # Calculamos la mora para la respuesta
                mora = cuota_actual.calcular_mora()
                
                # Guardamos los cambios
                cuota_actual.save()

            return Response({
                'status': 'Pago registrado con éxito y reflejado en caja',
                'cuota': cuota_actual.numero_cuota,
                'mora_aplicada': float(mora),
                'monto_total_recibido': float(cuota_actual.monto_total + mora)
            }, status=status.HTTP_200_OK)

        except Exception as e:
            # Si algo sale mal (como el error de atributo que tuviste antes), cae aquí
            return Response(
                {
                    'error': 'Error crítico al procesar el pago en el servidor.',
                    'detalle': str(e)
                }, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
    @action(detail=True, methods=['get'])
    def generar_recibo(self, request, pk=None):
        cuota = self.get_object()
        if not cuota.esta_pagada:
            return Response({'error': 'No se puede generar recibo de una cuota no pagada'}, status=400)

        # Crear el PDF en memoria
        buffer = BytesIO()
        p = canvas.Canvas(buffer)

        # Diseño del Recibo
        p.setFont("Helvetica-Bold", 16)
        p.drawString(100, 800, f"RECIBO DE PAGO - Cuota #{cuota.numero_cuota}")
        
        p.setFont("Helvetica", 12)
        p.drawString(100, 770, f"Cliente: {cuota.prestamo.cliente.nombre} {cuota.prestamo.cliente.apellido}")
        p.drawString(100, 750, f"Fecha de Pago: {cuota.fecha_pago_real}")
        p.drawString(100, 730, f"Monto Cuota: ${cuota.monto_total}")
        p.drawString(100, 710, f"Mora Pagada: ${cuota.calcular_mora()}")
        p.line(100, 690, 500, 690)
        p.drawString(100, 670, f"TOTAL RECIBIDO: ${cuota.monto_total + cuota.calcular_mora()}")

        p.showPage()
        p.save()

        buffer.seek(0)
        return HttpResponse(buffer, content_type='application/pdf', 
                            headers={'Content-Disposition': f'attachment; filename="Recibo_Cuota_{cuota.id}.pdf"'})


class CajaViewSet(viewsets.ModelViewSet):
    queryset = Caja.objects.all().order_by('-fecha') # Los últimos movimientos primero
    serializer_class = CajaSerializer