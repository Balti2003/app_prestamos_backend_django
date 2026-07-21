import io
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

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
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#07080a"),
        alignment=1,
        spaceAfter=15
    )
    label_style = ParagraphStyle('LabelStyle', parent=styles['Normal'], fontSize=10, leading=12, textColor=colors.HexColor("#4b5563"))
    value_style = ParagraphStyle('ValueStyle', parent=styles['Normal'], fontSize=10, leading=12, fontName="Helvetica-Bold", textColor=colors.HexColor("#111827"))

    # Encabezado
    story.append(Paragraph("<b>COMPROBANTE DE DESEMBOLSO DE PRÉSTAMO</b>", title_style))
    
    # Obtener fecha de inicio o creación de forma segura
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

    # 1. Buscamos primero la cuota real generada en la base de datos
    monto_cuota = 0
    if hasattr(prestamo, 'cuota_set') and prestamo.cuota_set.exists():
        primera_cuota = prestamo.cuota_set.first()
        monto_cuota = float(primera_cuota.monto_total)
    
    # 2. Si por alguna razón no hay cuotas en DB, aplicamos tu misma fórmula exacta
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

    # Texto de conformidad y espacio de firma
    texto_conformidad = "Declaro haber recibido en conformidad el dinero en efectivo detallado anteriormente en concepto de desembolso de préstamo."
    story.append(Paragraph(f"<i>{texto_conformidad}</i>", label_style))
    story.append(Spacer(1, 50))

    tabla_firma = [
        [Paragraph("___________________________________", ParagraphStyle('C', alignment=1)), Paragraph("___________________________________", ParagraphStyle('C', alignment=1))],
        [Paragraph("<b>Firma del Cliente</b>", ParagraphStyle('C', alignment=1, fontSize=9)), Paragraph("<b>Firma y Sello Financiera</b>", ParagraphStyle('C', alignment=1, fontSize=9))]
    ]
    t_firma = Table(tabla_firma, colWidths=[250, 250])
    story.append(t_firma)

    doc.build(story)
    buffer.seek(0)
    return buffer