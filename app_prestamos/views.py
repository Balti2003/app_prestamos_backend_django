from xml.dom import ValidationErr
from rest_framework import viewsets, status, filters, parsers
from rest_framework.response import Response
from .models import Cliente, Prestamo, Cuota, Caja, HistorialCuota, CajaDiaria, GarantiaCliente
from .serializers import ClienteSerializer, PrestamoSerializer, CuotaSerializer, CajaSerializer, ClientePerfilSerializer, CajaDiariaSerializer, GarantiaClienteSerializer
from rest_framework.decorators import action
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from .filters import PrestamoFilter, CuotaFilter
from django.db import transaction
from django.http import HttpResponse
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from django.shortcuts import get_object_or_404
from decimal import Decimal, InvalidOperation
from django.db.models import Sum
from rest_framework.permissions import IsAuthenticated
from .serializers import CambiarPasswordSerializer
from rest_framework.views import APIView
from .utils import generar_pdf_desembolso_seguro


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.filter(activo=True)
    
    def get_serializer_class(self):
        # Si están pidiendo el detalle de un cliente
        if self.action == 'retrieve':
            return ClientePerfilSerializer
        # Para el listado general (/api/clientes/) usa el básico de siempre
        return ClienteSerializer

    @action(detail=True, methods=['get'])
    def cuotas_cobrables(self, request, pk=None):
        cliente = self.get_object()
        # Filtramos préstamos activos o en mora
        prestamos = cliente.prestamos.filter(estado__in=['activo', 'mora'], activo=True)
        
        proximas_cuotas = []
        hoy = timezone.localdate()

        for p in prestamos:
            cuota = p.cuotas.filter(esta_pagada=False).order_by('numero_cuota').first()
            
            if cuota:
                monto_base = cuota.monto_total
                mora_acumulada = Decimal('0.00')
                dias_atraso = 0

                if cuota.fecha_vencimiento < hoy:
                    dias_atraso = (hoy - cuota.fecha_vencimiento).days
                    # Calculamos mora (ejemplo 1% diario)
                    tasa_diaria = Decimal('0.01') 
                    mora_acumulada = monto_base * tasa_diaria * dias_atraso

                proximas_cuotas.append({
                    "cuota_id": cuota.id,
                    "prestamo_id": p.id,
                    "numero_cuota": cuota.numero_cuota,
                    "monto_base": str(monto_base),
                    "monto": str(monto_base + mora_acumulada),
                    "mora": str(mora_acumulada),
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
    queryset = Prestamo.objects.filter(activo=True)
    serializer_class = PrestamoSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = PrestamoFilter
    search_fields = ['cliente__nombre', 'cliente__apellido', 'cliente__dni']
    ordering_fields = ['fecha_inicio', 'monto_solicitado']
    
    # Sobrescribimos el método create para disparar la lógica de cuotas
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        
        if not data.get('fecha_inicio'):
            data['fecha_inicio'] = timezone.now()
        
        serializer = self.get_serializer(data=data)
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

    @action(detail=True, methods=['get'], url_path='comprobante-desembolso')
    def generar_comprobante_desembolso(self, request, pk=None):
        try:
            prestamo = self.get_object()
            operador = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
            
            pdf_buffer = generar_pdf_desembolso_seguro(prestamo, operador_nombre=operador)
            
            response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="Comprobante_Desembolso_{prestamo.id}.pdf"'
            return response
        except Exception as e:
            print(f"Error interno al generar PDF de desembolso: {e}")
            return HttpResponse(f"Error al generar PDF: {str(e)}", status=500)


    @action(detail=False, methods=['post'])
    def registrar_pago_exacto(self, request):
        cuota_id = request.data.get('cuota_id')
        monto_raw = request.data.get('monto') # Capturamos el dato crudo
        
        if not cuota_id:
            return Response({"error": "Debe proporcionar el ID de la cuota."}, status=400)
        
        # --- VALIDACIÓN DE MONTO (Parte 1 robustecida) ---
        if monto_raw is None or monto_raw == "":
            return Response({"error": "El monto del pago es obligatorio y no fue recibido."}, status=400)

        try:
            # Limpiamos el valor por si el frontend envía el signo $ o comas de miles
            monto_limpio = str(monto_raw).replace('$', '').replace(',', '').strip()
            monto_enviado = Decimal(monto_limpio)
        except (InvalidOperation, ValueError, TypeError):
            return Response({"error": f"Formato de monto inválido recibido: {monto_raw}"}, status=400)
        # -------------------------------------------------

        cuota = get_object_or_404(Cuota, id=cuota_id, esta_pagada=False)
        prestamo = cuota.prestamo
        monto_base = cuota.monto_total

        # Calculamos la mora en base al monto verificado
        mora_calculada = monto_enviado - monto_base

        # 1. Procesamos el pago guardando la mora de forma fija
        cuota.esta_pagada = True
        cuota.fecha_pago_real = timezone.localdate() # Simplificado para fecha local
        cuota.mora_pagada = max(Decimal('0.00'), mora_calculada) 
        cuota.save()

        # 2. Forzamos al préstamo a revisar su estado
        if hasattr(prestamo, 'actualizar_estado_mora'):
            prestamo.actualizar_estado_mora() 

        return Response({
            "success": True,
            "message": "Pago asentado correctamente.",
            "cuota_id": cuota.id,
            "monto_total": str(monto_enviado),
            "mora_asentada": str(cuota.mora_pagada)
        }, status=200)
        
        
    @action(detail=False, methods=['post'])
    def sincronizar_mora(self, request):
        prestamos_activos = Prestamo.objects.filter(estado__in=['activo', 'mora'])
        actualizados = 0
            
        for p in prestamos_activos:
            if p.actualizar_estado_mora():
                actualizados += 1
                    
        return Response({
            "message": f"Sincronización completada. {actualizados} préstamos cambiaron de estado."
        })


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
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        
    @action(detail=True, methods=['get'])
    def generar_recibo(self, request, pk=None):
        cuota = self.get_object()
        if not cuota.esta_pagada:
            return Response({'error': 'No se puede generar recibo de una cuota no pagada'}, status=400)

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
        styles = getSampleStyleSheet()
        elements = []

        # --- ENCABEZADO ---
        titulo_style = ParagraphStyle('TituloStyle', parent=styles['Heading1'], fontSize=18, alignment=1, spaceAfter=20)
        elements.append(Paragraph("COMPROBANTE DE PAGO", titulo_style))
        elements.append(Paragraph("<b>Sistema de Gestión de Préstamos</b>", styles['Normal']))
        fecha_local = timezone.localtime(timezone.now())
        elements.append(Paragraph(f"Fecha de emisión: {fecha_local.strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
        elements.append(Spacer(1, 20))

        # --- DATOS DEL CLIENTE Y PRÉSTAMO ---
        data_cliente = [
            [Paragraph(f"<b>Cliente:</b> {cuota.prestamo.cliente.nombre} {cuota.prestamo.cliente.apellido}", styles['Normal']), 
             Paragraph(f"<b>DNI/CUIL:</b> {getattr(cuota.prestamo.cliente, 'dni', '---')}", styles['Normal'])],
            [Paragraph(f"<b>Préstamo ID:</b> #{cuota.prestamo.id}", styles['Normal']), 
             Paragraph(f"<b>Cuota N°:</b> {cuota.numero_cuota}", styles['Normal'])]
        ]
        t_cliente = Table(data_cliente, colWidths=[250, 200])
        elements.append(t_cliente)
        elements.append(Spacer(1, 20))

        # --- DETALLE DEL PAGO CORREGIDO ---
        mora = cuota.mora_pagada
        total = cuota.monto_total + mora
        
        data_pago = [
            ['Descripción', 'Monto'],
            ['Monto de la Cuota', f"${cuota.monto_total:,.2f}"],
            ['Intereses por Mora', f"${mora:,.2f}"],
            [Paragraph('<b>TOTAL PAGADO</b>', styles['Normal']), f'${total:,.2f}']
        ]

        t_pago = Table(data_pago, colWidths=[350, 100])
        t_pago.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (1, 0), 12),
            ('BACKGROUND', (0, 3), (1, 3), colors.lightgrey),
            ('GRID', (0, 0), (1, 3), 1, colors.black),
            ('ALIGN', (1, 1), (1, 3), 'RIGHT'),
        ]))
        elements.append(t_pago)
        elements.append(Spacer(1, 40))

        # --- FIRMA Y PIE ---
        elements.append(Paragraph(f"Cobrado por: {request.user.get_full_name() or request.user.username}", styles['Normal']))
        elements.append(Spacer(1, 30))
        elements.append(Paragraph("__________________________", styles['Normal']))
        elements.append(Paragraph("Firma y Sello del Receptor", styles['Normal']))
        
        elements.append(Spacer(1, 50))
        nota_style = ParagraphStyle('NotaStyle', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
        elements.append(Paragraph("Este documento sirve como comprobante legal de pago para el período mencionado. Conserve este recibo para cualquier reclamo futuro.", nota_style))

        doc.build(elements)
        buffer.seek(0)
        
        filename = f"Recibo_P#{cuota.prestamo.id}_C#{cuota.numero_cuota}.pdf"
        return HttpResponse(buffer, content_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})

class CajaViewSet(viewsets.ModelViewSet):
    queryset = Caja.objects.all().order_by('-fecha') # Los últimos movimientos primero
    serializer_class = CajaSerializer

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
    permission_classes = [IsAuthenticated]

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
        error_msg = list(serializer.errors.values())[0][0]
        return Response({"error": error_msg}, status=status.HTTP_400_BAD_REQUEST)

class CajaDiariaViewSet(viewsets.ModelViewSet):
    queryset = CajaDiaria.objects.all()
    serializer_class = CajaDiariaSerializer
    permission_classes = [IsAuthenticated]

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
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)