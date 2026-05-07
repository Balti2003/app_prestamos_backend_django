from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Sum
from django.utils import timezone
from .models import Prestamo, Cuota, Cliente, Caja

class DashboardResumenView(APIView):
    def get(self, request):
        hoy = timezone.now().date()
        
        # 1. Métricas de Capital (Lo que falta cobrar)
        capital_en_la_calle = Cuota.objects.filter(esta_pagada=False).aggregate(Sum('monto_capital'))['monto_capital__sum'] or 0
        intereses_por_cobrar = Cuota.objects.filter(esta_pagada=False).aggregate(Sum('monto_interes'))['monto_interes__sum'] or 0
        
        # 2. Estado de la Cartera
        prestamos_activos = Prestamo.objects.filter(activo=True, estado='activo').count()
        prestamos_en_mora = Prestamo.objects.filter(activo=True, estado='mora').count()
        
        # 3. Cobranza del Día
        cobranza_hoy_esperada = Cuota.objects.filter(
            fecha_vencimiento=hoy, 
            esta_pagada=False
        ).aggregate(Sum('monto_total'))['monto_total__sum'] or 0
        
        # 4. Caja y Rentabilidad REAL
        saldo_caja = Caja.saldo_actual()

        # --- LÓGICA DE RENTABILIDAD ---
        # Sumamos los intereses de todas las cuotas que ya fueron pagadas
        cuotas_pagadas = Cuota.objects.filter(esta_pagada=True)
        intereses_ya_cobrados = cuotas_pagadas.aggregate(Sum('monto_interes'))['monto_interes__sum'] or 0
        
        # Opcional: Si quieres incluir la mora como ganancia
        mora_ya_cobrada = sum(c.calcular_mora() for c in cuotas_pagadas)
        
        total_ganancia_real = intereses_ya_cobrados + mora_ya_cobrada
        # ------------------------------

        # 5. Tasa de Mora
        total_pendientes = Cuota.objects.filter(esta_pagada=False).count()
        vencidas = Cuota.objects.filter(esta_pagada=False, fecha_vencimiento__lt=hoy).count()
        tasa_mora = (vencidas / total_pendientes * 100) if total_pendientes > 0 else 0

        return Response({
            "metricas_financieras": {
                "capital_en_calle": float(capital_en_la_calle),
                "intereses_pendientes": float(intereses_por_cobrar),
                "saldo_caja_disponible": float(saldo_caja),
                "rentabilidad_acumulada": float(total_ganancia_real)
            },
            "estado_cartera": {
                "prestamos_activos": prestamos_activos,
                "prestamos_en_mora": prestamos_en_mora,
                "tasa_mora_porcentaje": round(tasa_mora, 2)
            },
            "operativo_hoy": {
                "cobros_pendientes_hoy": float(cobranza_hoy_esperada),
                "clientes_total": Cliente.objects.filter(activo=True).count() # Solo clientes activos
            }
        })