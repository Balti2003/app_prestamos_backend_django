import io
from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def generar_pdf_desembolso_seguro(prestamo, operador_nombre=""):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter, 
        rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40
    )
    story = []
    styles = getSampleStyleSheet()

    # Estilos
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#07080a"),
        alignment=1,
        spaceAfter=10
    )
    empresa_title_style = ParagraphStyle(
        'EmpresaTitle',
        parent=styles['Heading2'],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1e293b"),
        alignment=1,
        fontName="Helvetica-Bold",
        spaceAfter=4
    )
    empresa_sub_style = ParagraphStyle(
        'EmpresaSub',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748b"),
        alignment=1,
        spaceAfter=12
    )
    label_style = ParagraphStyle('LabelStyle', parent=styles['Normal'], fontSize=10, leading=12, textColor=colors.HexColor("#4b5563"))
    value_style = ParagraphStyle('ValueStyle', parent=styles['Normal'], fontSize=10, leading=12, fontName="Helvetica-Bold", textColor=colors.HexColor("#111827"))

    # Datos de la Empresa (Tenant)
    empresa = getattr(prestamo, 'empresa', None)
    nombre_empresa = getattr(empresa, 'nombre', 'PRESTAYA SERVICIOS FINANCIEROS')
    cuit_empresa = getattr(empresa, 'cuit_rut', '')

    # Encabezado Membretado
    story.append(Paragraph(nombre_empresa.upper(), empresa_title_style))
    if cuit_empresa:
        story.append(Paragraph(f"CUIT/RUT: {cuit_empresa}", empresa_sub_style))
    else:
        story.append(Spacer(1, 6))

    story.append(Paragraph("<b>COMPROBANTE DE DESEMBOLSO DE PRÉSTAMO</b>", title_style))
    
    # Obtener fecha de inicio o creación
    fecha_str = getattr(prestamo, 'fecha_inicio', None) or getattr(prestamo, 'fecha_creacion', None)
    if hasattr(fecha_str, 'strftime'):
        fecha_str = fecha_str.strftime('%d/%m/%Y')
    else:
        fecha_str = str(fecha_str or 'N/A')

    story.append(Paragraph(f"<b>Operación N°:</b> #{prestamo.id} | <b>Fecha de Emisión:</b> {fecha_str}", label_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb"), spaceAfter=15, spaceBefore=10))

    # Datos del Cliente
    cliente = getattr(prestamo, 'cliente', None)
    nombre_cliente = f"{getattr(cliente, 'nombre', '')} {getattr(cliente, 'apellido', '')}".strip() if cliente else "N/A"
    dni_cliente = getattr(cliente, 'dni', 'N/A')
    telefono_cliente = getattr(cliente, 'telefono', 'N/A')

    datos_cliente = [
        [Paragraph("<b>Titular:</b>", label_style), Paragraph(nombre_cliente, value_style)],
        [Paragraph("<b>DNI/CUIL:</b>", label_style), Paragraph(str(dni_cliente), value_style)],
        [Paragraph("<b>Teléfono:</b>", label_style), Paragraph(str(telefono_cliente), value_style)],
    ]
    t_cliente = Table(datos_cliente, colWidths=[100, 400])
    t_cliente.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
    story.append(t_cliente)
    
    story.append(Spacer(1, 15))

    # Detalle del Préstamo
    monto_solicitado = float(prestamo.monto_solicitado)
    cuotas_totales = prestamo.cuotas_totales
    tasa_interes = float(prestamo.tasa_interes)

    monto_cuota = 0
    if hasattr(prestamo, 'cuota_set') and prestamo.cuota_set.exists():
        primera_cuota = prestamo.cuota_set.first()
        monto_cuota = float(primera_cuota.monto_total)
    
    if monto_cuota == 0 and cuotas_totales > 0:
        interes_total = monto_solicitado * (tasa_interes / 100.0)
        monto_total_a_pagar = monto_solicitado + interes_total
        monto_cuota = monto_total_a_pagar / cuotas_totales

    datos_prestamo = [
        [Paragraph("Concepto", label_style), Paragraph("Detalle", label_style)],
        [Paragraph("Capital Total Entregado", label_style), Paragraph(f"<b>${monto_solicitado:,.2f}</b>", value_style)],
        [Paragraph("Plan de Pagos Pactado", label_style), Paragraph(f"{cuotas_totales} cuotas de <b>${monto_cuota:,.2f}</b>", value_style)],
        [Paragraph("Operador / Emisor", label_style), Paragraph(operador_nombre or "Sistema", value_style)]
    ]
    t_prestamo = Table(datos_prestamo, colWidths=[200, 300])
    t_prestamo.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f3f4f6")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#d1d5db")),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_prestamo)

    story.append(Spacer(1, 35))

    texto_conformidad = "Declaro haber recibido en conformidad el dinero en efectivo detallado anteriormente en concepto de desembolso de préstamo."
    story.append(Paragraph(f"<i>{texto_conformidad}</i>", label_style))
    story.append(Spacer(1, 50))

    tabla_firma = [
        [Paragraph("___________________________________", ParagraphStyle('C', alignment=1)), Paragraph("___________________________________", ParagraphStyle('C', alignment=1))],
        [Paragraph("<b>Firma del Cliente</b>", ParagraphStyle('C', alignment=1, fontSize=9)), Paragraph(f"<b>Firma y Sello - {nombre_empresa}</b>", ParagraphStyle('C', alignment=1, fontSize=9))]
    ]
    t_firma = Table(tabla_firma, colWidths=[250, 250])
    story.append(t_firma)

    doc.build(story)
    buffer.seek(0)
    return buffer


def generar_recibo_pago_pdf(cuota, cobrador_nombre="Sistema"):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
    styles = getSampleStyleSheet()
    elements = []

    # Datos de la Empresa (Tenant)
    empresa = getattr(cuota.prestamo, 'empresa', None)
    nombre_empresa = getattr(empresa, 'nombre', 'PRESTAYA SERVICIOS FINANCIEROS')
    cuit_empresa = getattr(empresa, 'cuit_rut', '')

    # --- ENCABEZADO MEMBRETADO ---
    empresa_title_style = ParagraphStyle(
        'EmpresaTitle',
        parent=styles['Heading1'],
        fontSize=18,
        alignment=1,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=2
    )
    cuit_style = ParagraphStyle('CuitStyle', parent=styles['Normal'], fontSize=9, alignment=1, textColor=colors.HexColor("#64748b"), spaceAfter=12)
    titulo_style = ParagraphStyle('TituloStyle', parent=styles['Heading2'], fontSize=14, alignment=1, spaceAfter=15, textColor=colors.HexColor("#0f172a"))

    elements.append(Paragraph(nombre_empresa.upper(), empresa_title_style))
    if cuit_empresa:
        elements.append(Paragraph(f"CUIT/RUT: {cuit_empresa}", cuit_style))
    
    elements.append(Paragraph("<b>COMPROBANTE OFICIAL DE PAGO</b>", titulo_style))
    
    fecha_local = timezone.localtime(timezone.now())
    elements.append(Paragraph(f"Fecha y hora de emisión: {fecha_local.strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
    elements.append(Spacer(1, 15))

    # --- DATOS DEL CLIENTE Y PRÉSTAMO ---
    estado_cuota = "COMPLETADA / SALDADA" if cuota.esta_pagada else "PAGO PARCIAL"
    cliente = cuota.prestamo.cliente
    dni_cliente = getattr(cliente, 'dni', '---')
    
    data_cliente = [
        [Paragraph(f"<b>Cliente:</b> {cliente.nombre} {cliente.apellido}", styles['Normal']), 
         Paragraph(f"<b>DNI/CUIL:</b> {dni_cliente}", styles['Normal'])],
        [Paragraph(f"<b>Préstamo ID:</b> #{cuota.prestamo.id}", styles['Normal']), 
         Paragraph(f"<b>Cuota N°:</b> {cuota.numero_cuota} <i>({estado_cuota})</i>", styles['Normal'])]
    ]
    t_cliente = Table(data_cliente, colWidths=[250, 200])
    elements.append(t_cliente)
    elements.append(Spacer(1, 20))

    # --- CÁLCULOS DEL DETALLE DE PAGO ---
    monto_pagado = getattr(cuota, 'monto_pagado', Decimal('0.00'))
    mora_pagada = getattr(cuota, 'mora_pagada', Decimal('0.00'))
    monto_total_cuota = getattr(cuota, 'monto_total', Decimal('0.00'))
    saldo_pendiente = getattr(cuota, 'saldo_pendiente', max(Decimal('0.00'), monto_total_cuota - monto_pagado))
    total_abonado = monto_pagado + mora_pagada

    metodo_raw = str(getattr(cuota, 'metodo_pago', 'efectivo') or 'efectivo').strip()
    if metodo_raw.lower() == 'efectivo':
        metodo_pago_str = 'EFECTIVO'
    elif metodo_raw.lower() == 'transferencia':
        metodo_pago_str = 'TRANSFERENCIA'
    elif metodo_raw.lower() == 'otro':
        metodo_pago_str = 'OTRO'
    else:
        metodo_pago_str = metodo_raw.upper()

    # --- TABLA DE DETALLE DEL PAGO ---
    data_pago = [
        ['Descripción', 'Monto / Detalle'],
        ['Monto Total de la Cuota', f"${monto_total_cuota:,.2f}"],
        ['Abono Realizado a Capital', f"${monto_pagado:,.2f}"],
        ['Intereses por Mora Abonados', f"${mora_pagada:,.2f}"],
        [Paragraph('<b>TOTAL ABONADO</b>', styles['Normal']), f'${total_abonado:,.2f}'],
        ['Forma de Pago', metodo_pago_str]
    ]

    if not cuota.esta_pagada and saldo_pendiente > 0:
        data_pago.append(['Saldo Restante Pendiente', f"${saldo_pendiente:,.2f}"])

    t_pago = Table(data_pago, colWidths=[350, 100])
    cant_filas = len(data_pago)
    t_pago_style = [
        ('BACKGROUND', (0, 0), (1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (1, 0), 12),
        ('BACKGROUND', (0, 4), (1, 4), colors.lightgrey),
        ('GRID', (0, 0), (1, cant_filas - 1), 1, colors.black),
        ('ALIGN', (1, 1), (1, cant_filas - 1), 'RIGHT'),
    ]

    if not cuota.esta_pagada and saldo_pendiente > 0:
        t_pago_style.append(('BACKGROUND', (0, 6), (1, 6), colors.HexColor("#FFF9C4")))

    t_pago.setStyle(TableStyle(t_pago_style))
    elements.append(t_pago)
    elements.append(Spacer(1, 40))

    # --- FIRMA Y PIE ---
    elements.append(Paragraph(f"Cobrado por: {cobrador_nombre}", styles['Normal']))
    elements.append(Spacer(1, 30))
    elements.append(Paragraph("__________________________", styles['Normal']))
    elements.append(Paragraph(f"Firma y Sello - {nombre_empresa}", styles['Normal']))
    
    elements.append(Spacer(1, 50))
    nota_style = ParagraphStyle('NotaStyle', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
    elements.append(Paragraph(
        "Este documento sirve como comprobante legal de pago/abono efectuado para el período mencionado. Conserve este recibo para cualquier reclamo futuro.", 
        nota_style
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer