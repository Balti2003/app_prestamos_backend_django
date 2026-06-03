from rest_framework import viewsets, status, filters
from rest_framework.response import Response
from .models import Cliente, Prestamo, Cuota, Caja, HistorialCuota
from .serializers import ClienteSerializer, PrestamoSerializer, CuotaSerializer, CajaSerializer
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
from decimal import Decimal


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.filter(activo=True)
    serializer_class = ClienteSerializer

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

class PrestamoViewSet(viewsets.ModelViewSet):
    queryset = Prestamo.objects.filter(activo=True)
    serializer_class = PrestamoSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = PrestamoFilter
    search_fields = ['cliente__nombre', 'cliente__apellido', 'cliente__dni']
    ordering_fields = ['fecha_inicio', 'monto_solicitado']
    
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


    @action(detail=False, methods=['post'])
    def registrar_pago_exacto(self, request):
        cuota_id = request.data.get('cuota_id')
        monto_enviado = request.data.get('monto')
        
        if not cuota_id:
            return Response({"error": "Debe proporcionar el ID de la cuota."}, status=400)
            
        cuota = get_object_or_404(Cuota, id=cuota_id, esta_pagada=False)
        prestamo = cuota.prestamo

        # Calculamos la mora en base a lo que realmente trajo el cliente
        monto_base = cuota.monto_total
        mora_calculada = Decimal(str(monto_enviado)) - monto_base

        # 1. Procesamos el pago guardando la mora de forma fija
        cuota.esta_pagada = True
        cuota.fecha_pago_real = timezone.localtime(timezone.now()).date()
        cuota.mora_pagada = max(Decimal('0.00'), mora_calculada) # Evitamos números negativos
        cuota.save()

        # 2. Registramos el movimiento en Caja por el monto TOTAL (9120)
        Caja.objects.create(
            tipo='ingreso',
            monto=Decimal(str(monto_enviado)),
            descripcion=f"Cobro Cuota #{cuota.numero_cuota} - Préstamo #{prestamo.id} (Mora: ${cuota.mora_pagada})"
        )

        # 3. Forzamos al préstamo a revisar su estado para que vuelva a estar "activo"
        prestamo.actualizar_estado_mora() 

        return Response({
            "success": True,
            "message": "Pago asentado correctamente.",
            "cuota_id": cuota.id
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