from django.db.models import Avg, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Caja, Cliente, Cuota, Prestamo


class DashboardResumenView(APIView):
    def get(self, request):
        hoy = timezone.localdate()
        prestamos_a_revisar = Prestamo.objects.filter(estado='activo', activo=True)
        
        for p in prestamos_a_revisar:
            # Si tiene alguna cuota vencida no paga, pasamos el préstamo a mora
            if p.cuotas.filter(esta_pagada=False, fecha_vencimiento__lt=hoy).exists():
                p.estado = 'mora'
                p.save()
                
        # 1. Métricas de Capital
        pendientes = Cuota.objects.filter(esta_pagada=False).aggregate(
            cap=Sum('monto_capital'),
            int=Sum('monto_interes')
        )
        capital_en_la_calle = pendientes['cap'] or 0
        intereses_por_cobrar = pendientes['int'] or 0
        
        # 1. Préstamos vigentes
        prestamos_vigentes_qs = Prestamo.objects.filter(activo=True, estado__in=['activo', 'mora'])
        prestamos_vigentes_totales = prestamos_vigentes_qs.count()
        
        prestamos_en_mora = prestamos_vigentes_qs.filter(estado='mora').count()
        
        # 2. Promedio calculado SOLO sobre préstamos vigentes
        promedio_otorgado = prestamos_vigentes_qs.aggregate(
            prom=Avg('monto_solicitado')
        )['prom'] or 0

        # 3. Cobranza del Día
        cobranza_hoy_esperada = Cuota.objects.filter(
            fecha_vencimiento=hoy, 
            esta_pagada=False
        ).aggregate(total=Sum('monto_total'))['total'] or 0
        
        # 4. Caja y Rentabilidad REAL
        saldo_caja = Caja.saldo_actual()

        datos_pagados = Cuota.objects.filter(esta_pagada=True).aggregate(
            int_cobrado=Sum('monto_interes'),
            mora_cobrada=Sum('mora_pagada')
        )
        
        total_ganancia_real = (datos_pagados['int_cobrado'] or 0) + (datos_pagados['mora_cobrada'] or 0)

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
                "prestamos_activos": prestamos_vigentes_totales,
                "promedio_prestamo": round(float(promedio_otorgado), 2),
                "prestamos_en_mora": prestamos_en_mora,
                "tasa_mora_porcentaje": round(tasa_mora, 2)
            },
            "operativo_hoy": {
                "cobros_pendientes_hoy": float(cobranza_hoy_esperada),
                "clientes_total": Cliente.objects.filter(activo=True).count()
            }
        })