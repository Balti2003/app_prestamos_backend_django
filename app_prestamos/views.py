from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.dom import ValidationErr

from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from rest_framework import filters, parsers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .filters import CajaFilter, CuotaFilter, PrestamoFilter
from .models import (
    Caja,
    CajaDiaria,
    Cliente,
    Cuota,
    GarantiaCliente,
    HistorialCuota,
    Prestamo,
)
from .serializers import (
    CajaDiariaSerializer,
    CajaSerializer,
    CambiarPasswordSerializer,
    ClientePerfilSerializer,
    ClienteResumenSerializer,
    ClienteSerializer,
    CuotaSerializer,
    GarantiaClienteSerializer,
    PrestamoSerializer,
    UserSerializer,
)
from .utils import generar_pdf_desembolso_seguro, generar_recibo_pago_pdf


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def usuario_actual(request):
    """Devuelve los datos del usuario logueado con su rol"""
    serializer = UserSerializer(request.user)
    return Response(serializer.data)

class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.filter(activo=True).order_by('-id')
    
    filter_backends = [filters.SearchFilter]  # noqa: RUF012
    search_fields = ['nombre', 'apellido', 'dni'] # noqa: RUF012
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ClientePerfilSerializer
        return ClienteSerializer

    def destroy(self, request, *args, **kwargs):
        cliente = self.get_object()
        
        # Validación de seguridad: No eliminar si tiene préstamos activos
        prestamos_activos = cliente.prestamos.filter(estado__in=['activo', 'mora'])
        if prestamos_activos.exists():
            return Response(
                {"error": "No se puede eliminar el cliente porque tiene préstamos activos o en mora."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        return super().destroy(request, *args, **kwargs)
    
    @action(detail=False, methods=['get'], pagination_class=None)
    def todos(self, request):
        """Retorna el listado completo sin paginar para selectores en modales"""
        clientes = self.filter_queryset(self.get_queryset())
        serializer = ClienteResumenSerializer(clientes, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def cuotas_cobrables(self, request, pk=None):
        cliente = self.get_object()
        prestamos = cliente.prestamos.filter(estado__in=['activo', 'mora'], activo=True)
        
        proximas_cuotas = []
        hoy = timezone.localdate()

        for p in prestamos:
            cuotas_pendientes = p.cuotas.filter(esta_pagada=False).order_by('numero_cuota')
            
            for cuota in cuotas_pendientes:
                # Lectura ultra segura de importes con fallback
                monto_total = Decimal(str(getattr(cuota, 'monto_total', Decimal('0.00')) or '0.00'))
                monto_pagado = Decimal(str(getattr(cuota, 'monto_pagado', Decimal('0.00')) or '0.00'))
                
                # Saldo de capital remanente
                if hasattr(cuota, 'saldo_pendiente') and cuota.saldo_pendiente is not None:
                    saldo_capital = Decimal(str(cuota.saldo_pendiente))
                else:
                    saldo_capital = max(Decimal('0.00'), monto_total - monto_pagado)
                
                mora_acumulada = Decimal('0.00')
                dias_atraso = 0

                if cuota.fecha_vencimiento and cuota.fecha_vencimiento < hoy:
                    dias_atraso = (hoy - cuota.fecha_vencimiento).days
                    
                    if hasattr(cuota, 'calcular_mora'):
                        mora_calc = Decimal(str(cuota.calcular_mora() or '0.00'))
                        mora_pagada = Decimal(str(getattr(cuota, 'mora_pagada', Decimal('0.00')) or '0.00'))
                        mora_acumulada = max(Decimal('0.00'), mora_calc - mora_pagada)
                    else:
                        tasa_diaria = Decimal('0.01') 
                        mora_acumulada = monto_total * tasa_diaria * Decimal(str(dias_atraso))

                monto_total_cobrable = saldo_capital + mora_acumulada

                proximas_cuotas.append({
                    "cuota_id": cuota.id,
                    "prestamo_id": p.id,
                    "numero_cuota": cuota.numero_cuota,
                    "monto_total": str(monto_total),
                    "monto_pagado": str(monto_pagado),
                    "saldo_pendiente": str(saldo_capital),
                    "mora": str(mora_acumulada),
                    "monto": str(monto_total_cobrable),
                    "dias_atraso": dias_atraso,
                    "fecha_vencimiento": cuota.fecha_vencimiento,
                    "prestamo_nombre": f"Préstamo #{p.id}"
                })
        
        return Response(proximas_cuotas)
    

class GarantiaClienteViewSet(viewsets.ModelViewSet):
    queryset = GarantiaCliente.objects.all()
    serializer_class = GarantiaClienteSerializer
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        queryset = super().get_queryset()
        cliente_id = self.request.query_params.get('cliente')
        if cliente_id:
            queryset = queryset.filter(cliente_id=cliente_id)
        return queryset
    
    
class PrestamoViewSet(viewsets.ModelViewSet):
    queryset = Prestamo.objects.filter(activo=True).order_by('-id')
    serializer_class = PrestamoSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]  # noqa: RUF012
    filterset_class = PrestamoFilter
    search_fields = ['cliente__nombre', 'cliente__apellido', 'cliente__dni']  # noqa: RUF012
    ordering_fields = ['fecha_inicio', 'monto_solicitado']  # noqa: RUF012
    
    def destroy(self, request, *args, **kwargs):
        prestamo = self.get_object()
        
        Caja.objects.filter(prestamo=prestamo).delete()

        return super().destroy(request, *args, **kwargs)
    
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        
        if not data.get('fecha_inicio'):
            data['fecha_inicio'] = timezone.now()
        
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        monto_solicitado = serializer.validated_data['monto_solicitado']
        saldo_disponible = Caja.saldo_actual()

        if saldo_disponible < monto_solicitado:
            return Response(
                {
                    "error": "Fondos insuficientes en caja.",
                    "saldo_actual": float(saldo_disponible),
                    "monto_requerido": float(monto_solicitado)
                }, 
                status=status.HTTP_400_BAD_REQUEST
            )

        prestamo = serializer.save()
        prestamo.generar_plan_pagos()
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['get'], url_path='comprobante-desembolso')
    def generar_comprobante_desembolso(self, request, pk=None):
        try:
            prestamo = self.get_object()
            operador = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
            
            pdf_buffer = generar_pdf_desembolso_seguro(prestamo, operador_nombre=operador)
            
            response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="Comprobante_Desembolso_{prestamo.id}.pdf"'
            return response
        except Exception as e:  # noqa: BLE001
            print(f"Error interno al generar PDF de desembolso: {e}")
            return HttpResponse(f"Error al generar PDF: {e!s}", status=500)

    @action(detail=True, methods=['post'], url_path='registrar-pago')
    def registrar_pago(self, request, pk=None):
        """
        Procesa el ingreso de dinero para un préstamo aplicando cobro en cascada:
        1. Mora de la cuota vencida más antigua.
        2. Saldo de capital/interés pendiente de esa cuota.
        3. El sobrante continúa hacia las siguientes cuotas (pagos parciales / adelantados).
        4. Asienta el ingreso del dinero real percibido en la caja con su método/detalle de pago.
        """
        prestamo = self.get_object()
        monto_raw = request.data.get('monto')
        
        metodo_pago_raw = request.data.get('metodo_pago', 'efectivo')
        metodo_pago_detalle = str(request.data.get('metodo_pago_detalle', '')).strip()

        if str(metodo_pago_raw).lower() == 'otro' and metodo_pago_detalle:
            metodo_pago_final = metodo_pago_detalle
        else:
            metodo_pago_final = metodo_pago_raw
        
        if monto_raw is None or monto_raw == "":
            return Response({"error": "Debe proporcionar el monto del pago."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            monto_limpio = str(monto_raw).replace('$', '').replace(',', '').strip()
            monto_disponible = Decimal(monto_limpio)
            if monto_disponible <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError, TypeError):
            return Response({"error": f"Formato de monto inválido: {monto_raw}"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            cuotas_pendientes = prestamo.cuotas.filter(esta_pagada=False).order_by('numero_cuota')

            if not cuotas_pendientes.exists():
                return Response({"error": "Este préstamo no tiene cuotas pendientes de pago."}, status=status.HTTP_400_BAD_REQUEST)

            monto_inicial = monto_disponible
            desglose = []

            for cuota in cuotas_pendientes:
                if monto_disponible <= 0:
                    break

                mora_cuota = cuota.calcular_mora()
                mora_abonada = Decimal('0.00')
                capital_abonado = Decimal('0.00')

                # A. Cobrar mora si existe
                if mora_cuota > 0:
                    mora_abonada = min(monto_disponible, mora_cuota)
                    cuota.mora_pagada += mora_abonada
                    monto_disponible -= mora_abonada

                # B. Cobrar capital/cuota si aún queda dinero disponible
                if monto_disponible > 0:
                    falta = cuota.saldo_pendiente
                    capital_abonado = min(monto_disponible, falta)
                    cuota.monto_pagado += capital_abonado
                    monto_disponible -= capital_abonado

                    if cuota.saldo_pendiente == Decimal('0.00'):
                        cuota.esta_pagada = True
                        cuota.fecha_pago_real = timezone.localdate()

                # Guardamos el método o detalle descriptivo en la cuota
                cuota.metodo_pago = metodo_pago_final
                cuota.save()

                desglose.append({
                    "cuota_id": cuota.id,
                    "numero_cuota": cuota.numero_cuota,
                    "mora_abonada": float(mora_abonada),
                    "capital_abonado": float(capital_abonado),
                    "cuota_saldada": cuota.esta_pagada,
                    "saldo_pendiente_cuota": float(cuota.saldo_pendiente)
                })

            # Calcular cuánto dinero real se aplicó del total entregado por el cliente
            monto_aplicado = monto_inicial - monto_disponible

            if monto_aplicado > 0:
                cliente = prestamo.cliente
                nombre_cliente = f"{cliente.apellido.upper()} {cliente.nombre.upper()}" if cliente else "CLIENTE"
                concepto = f"COBRO PAGO PREST #{prestamo.id} - CLIENTE: {nombre_cliente}"

                # Tomamos el ID de la primera cuota impactada en la cascada
                cuota_impactada_id = desglose[0]["cuota_id"] if desglose else None

                Caja.objects.create(
                    tipo='ingreso',
                    monto=monto_aplicado,
                    concepto=concepto,
                    metodo_pago=metodo_pago_final,
                    prestamo=prestamo,
                    cuota_id=cuota_impactada_id
                )

            # Actualizar estado de mora y vigencia del préstamo
            if hasattr(prestamo, 'actualizar_estado_mora'):
                prestamo.actualizar_estado_mora()

            # Verificar si se cancelaron todas las cuotas del contrato
            todas_pagadas = not prestamo.cuotas.filter(esta_pagada=False).exists()
            if todas_pagadas:
                prestamo.estado = 'finalizado'
                prestamo.save()

            return Response({
                "success": True,
                "message": "Pago procesado y registrado en caja correctamente.",
                "monto_ingresado": float(monto_inicial),
                "monto_aplicado": float(monto_aplicado),
                "monto_sobrante": float(monto_disponible),
                "prestamo_finalizado": todas_pagadas,
                "desglose": desglose
            }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def sincronizar_mora(self, request):
        prestamos_activos = Prestamo.objects.filter(estado__in=['activo', 'mora'])
        actualizados = 0
            
        for p in prestamos_activos:
            if hasattr(p, 'actualizar_estado_mora') and p.actualizar_estado_mora():
                actualizados += 1
                    
        return Response({
            "message": f"Sincronización completada. {actualizados} préstamos cambiaron de estado."
        })


class CuotaViewSet(viewsets.ModelViewSet):
    queryset = Cuota.objects.all()
    serializer_class = CuotaSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]  # noqa: RUF012
    filterset_class = CuotaFilter
    ordering_fields = ['fecha_vencimiento', 'numero_cuota']  # noqa: RUF012
    
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
                # Capturamos el usuario del Token
                # Si el token es válido, request.user es el objeto de tu Admin
                usuario_obj = request.user
                
                # Armamos el nombre para el historial
                if usuario_obj.is_authenticated:
                    # Intentamos nombre completo, si no, el username
                    nombre_operador = f"{usuario_obj.first_name} {usuario_obj.last_name}".strip()
                    operador = nombre_operador if nombre_operador else usuario_obj.username
                else:
                    operador = "Sistema (Token no detectado)"

                # Procesamos el pago
                cuota_actual.esta_pagada = True
                cuota_actual.fecha_pago_real = timezone.now().date()
                cuota_actual.save()

                # Creamos el Historial
                HistorialCuota.objects.create(
                    cuota=cuota_actual,
                    estado_anterior="Pendiente",
                    estado_nuevo="Pagada",
                    usuario=operador,
                    observaciones="Pago procesado con éxito vía API."
                )

            return Response({'status': 'Pago registrado con éxito'}, status=status.HTTP_200_OK)
        except Exception as e:  # noqa: BLE001
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        
    @action(detail=True, methods=['get'])
    def generar_recibo(self, request, pk=None):
        cuota = self.get_object()
        
        monto_pagado = getattr(cuota, 'monto_pagado', Decimal('0.00'))
        mora_pagada = getattr(cuota, 'mora_pagada', Decimal('0.00'))
        
        if monto_pagado <= 0 and mora_pagada <= 0 and not cuota.esta_pagada:
            return Response(
                {'error': 'No se puede generar recibo de una cuota que no registra ningún pago.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        cobrador = request.user.get_full_name() or request.user.username if request.user and request.user.is_authenticated else "Sistema"
        
        # Generación delegada a utils.py
        buffer = generar_recibo_pago_pdf(cuota, cobrador_nombre=cobrador)
        
        filename = f"Recibo_P#{cuota.prestamo.id}_C#{cuota.numero_cuota}.pdf"
        return HttpResponse(
            buffer, 
            content_type='application/pdf', 
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )


class CajaViewSet(viewsets.ModelViewSet):
    queryset = Caja.objects.all().order_by('-fecha', '-id')
    serializer_class = CajaSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]  # noqa: RUF012
    filterset_class = CajaFilter  
    search_fields = ['concepto']  # noqa: RUF012

class DashboardViewSet(viewsets.ViewSet):
    """
    Vista para obtener las estadísticas del Dashboard en tiempo real.
    """
    def list(self, request):
        hoy = timezone.localdate()
        
        # 1. Cobros esperados HOY (Suma de cuotas que vencen hoy y no están pagas)
        cobros_hoy = Cuota.objects.filter(
            fecha_vencimiento=hoy, 
            esta_pagada=False
        ).aggregate(total=Sum('monto_total'))['total'] or Decimal('0.00')

        # 2. Alerta Crítica (Cuentas que están actualmente en estado MORA)
        cuentas_mora = Prestamo.objects.filter(estado='mora', activo=True).count()

        # 3. Cartera Total (Clientes que tienen préstamos sin finalizar)
        cartera_total = Cliente.objects.filter(
            prestamos__estado__in=['activo', 'mora'],
            activo=True
        ).distinct().count()

        # 4. Saldo en Caja (Usando el método que ya tenés en tu modelo Caja)
        saldo_caja = Caja.saldo_actual()

        return Response({
            "cobros_esperados_hoy": float(cobros_hoy),
            "cuentas_en_mora": cuentas_mora,
            "cartera_total": cartera_total,
            "saldo_caja": float(saldo_caja),
            "ultima_actualizacion": timezone.localtime().strftime('%H:%M:%S')
        })

class CambiarPasswordView(APIView):
    permission_classes = [IsAuthenticated]  # noqa: RUF012

    def post(self, request, *args, **kwargs):
        serializer = CambiarPasswordSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            user = request.user
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            
            return Response(
                {"message": "Contraseña actualizada correctamente."}, 
                status=status.HTTP_200_OK
            )
            
        # Retornamos el primer error que encontremos para simplificar el mensaje en el frontend
        error_msg = next(iter(serializer.errors.values()))[0]
        return Response({"error": error_msg}, status=status.HTTP_400_BAD_REQUEST)

class CajaDiariaViewSet(viewsets.ModelViewSet):
    queryset = CajaDiaria.objects.all()
    serializer_class = CajaDiariaSerializer
    permission_classes = [IsAuthenticated]  # noqa: RUF012

    # Endpoint para consultar el estado de la caja actual
    @action(detail=False, methods=['get'])
    def estado_actual(self, request):
        caja_abierta = CajaDiaria.objects.filter(estado='ABIERTA').first()
        if caja_abierta:
            # Calculamos dinámicamente cómo va la caja en tiempo real
            ingresos = sum(mov.monto for mov in caja_abierta.movimientos.filter(tipo='ingreso'))
            egresos = sum(mov.monto for mov in caja_abierta.movimientos.filter(tipo='egreso'))
            estimado = caja_abierta.saldo_apertura + ingresos - egresos
            
            return Response({
                'caja_abierta': True,
                'id': caja_abierta.id,
                'fecha': caja_abierta.fecha,
                'saldo_apertura': caja_abierta.saldo_apertura,
                'ingresos_sistema': ingresos,
                'egresos_sistema': egresos,
                'saldo_estimado': estimado,
                'operador_apertura': caja_abierta.operador_apertura.username
            })
        
        # Si no hay caja abierta, buscamos el saldo del último cierre para proponerlo como apertura
        ultima_caja = CajaDiaria.objects.filter(estado='CERRADA').first()
        saldo_sugerido = ultima_caja.saldo_real_fisico if ultima_caja else 0.00
        
        return Response({
            'caja_abierta': False,
            'saldo_sugerido': saldo_sugerido
        })

    # Endpoint para abrir la caja diaria
    @action(detail=False, methods=['post'])
    def abrir_caja(self, request):
        saldo_inicial = request.data.get('saldo_apertura', 0.00)
        
        try:
            with transaction.atomic():
                nueva_caja = CajaDiaria(
                    operador_apertura=request.user,
                    saldo_apertura=saldo_inicial,
                    estado='ABIERTA'
                )
                nueva_caja.full_clean() # Valida que no haya otra abierta
                nueva_caja.save()
                
                serializer = self.get_serializer(nueva_caja)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ValidationErr as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # Endpoint para cerrar la caja del día (Arqueo)
    @action(detail=True, methods=['post'])
    def cerrar_caja(self, request, pk=None):
        caja = self.get_object()
        saldo_fisico = request.data.get('saldo_real_fisico')
        observaciones = request.data.get('observaciones', '')

        if not saldo_fisico:
            return Response({'error': 'Debes proveer el saldo real físico contado de la caja.'}, status=status.HTTP_400_BAD_REQUEST)

        if caja.estado == 'CERRADA':
            return Response({'error': 'Esta caja ya se encuentra cerrada.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # 1. Calculamos ingresos y egresos del día según los movimientos asociados
                ingresos = sum(mov.monto for mov in caja.movimientos.filter(tipo='ingreso'))
                egresos = sum(mov.monto for mov in caja.movimientos.filter(tipo='egreso'))
                
                caja.ingresos_sistema = ingresos
                caja.egresos_sistema = egresos
                caja.saldo_estimado = caja.saldo_apertura + ingresos - egresos
                
                # 2. Registramos el conteo físico y la diferencia (sobrante/faltante)
                caja.saldo_real_fisico = saldo_fisico
                caja.diferencia = caja.saldo_real_fisico - caja.saldo_estimado
                
                caja.observaciones = observaciones
                caja.operador_cierre = request.user
                caja.fecha_cierre = timezone.now()
                caja.estado = 'CERRADA'
                caja.save()

                serializer = self.get_serializer(caja)
                return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:  # noqa: BLE001
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)