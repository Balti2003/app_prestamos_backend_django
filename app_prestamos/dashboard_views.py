import calendar
from datetime import datetime
from decimal import Decimal

from django.db.models import Avg, Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Caja, Cliente, Cuota, Prestamo


class DashboardResumenView(APIView):
    permission_classes = [IsAuthenticated]  # noqa: RUF012

    def get(self, request):
        hoy = timezone.localdate()
        tz_actual = timezone.get_current_timezone()

        # Actualización preventiva de préstamos en mora
        prestamos_a_revisar = Prestamo.objects.filter(estado='activo', activo=True)
        for p in prestamos_a_revisar:
            if p.cuotas.filter(esta_pagada=False, fecha_vencimiento__lt=hoy).exists():
                p.estado = 'mora'
                p.save()
                
        # 1. Métricas de Capital
        pendientes = Cuota.objects.filter(esta_pagada=False).aggregate(
            cap=Sum('monto_capital'),
            int=Sum('monto_interes')
        )
        capital_en_la_calle = pendientes['cap'] or Decimal('0.00')
        intereses_por_cobrar = pendientes['int'] or Decimal('0.00')
        
        # Préstamos vigentes
        prestamos_vigentes_qs = Prestamo.objects.filter(activo=True, estado__in=['activo', 'mora'])
        prestamos_vigentes_totales = prestamos_vigentes_qs.count()
        prestamos_en_mora = prestamos_vigentes_qs.filter(estado='mora').count()
        
        # 2. Promedio sobre vigentes
        promedio_otorgado = prestamos_vigentes_qs.aggregate(
            prom=Avg('monto_solicitado')
        )['prom'] or Decimal('0.00')

        # 3. Cobranza esperada hoy
        cobranza_hoy_esperada = Cuota.objects.filter(
            fecha_vencimiento=hoy, 
            esta_pagada=False
        ).aggregate(total=Sum('monto_total'))['total'] or Decimal('0.00')
        
        # 4. Caja y Rentabilidad Real
        saldo_caja = Caja.saldo_actual()

        datos_pagados = Cuota.objects.filter(esta_pagada=True).aggregate(
            int_cobrado=Sum('monto_interes'),
            mora_cobrada=Sum('mora_pagada')
        )
        total_ganancia_real = (datos_pagados['int_cobrado'] or Decimal('0.00')) + (datos_pagados['mora_cobrada'] or Decimal('0.00'))

        # 5. Tasa de Mora
        total_pendientes = Cuota.objects.filter(esta_pagada=False).count()
        vencidas = Cuota.objects.filter(esta_pagada=False, fecha_vencimiento__lt=hoy).count()
        tasa_mora = (vencidas / total_pendientes * 100) if total_pendientes > 0 else 0

        # 6. Tendencias de Crecimiento (Últimos 6 meses reales)
        MESES_ABR = {
            1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr', 5: 'May', 6: 'Jun',
            7: 'Jul', 8: 'Ago', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'
        }

        tendencias_crecimiento = []

        for i in range(5, -1, -1):
            mes_target = hoy.month - i
            año_target = hoy.year
            while mes_target <= 0:
                mes_target += 12
                año_target -= 1

            _, ultimo_dia = calendar.monthrange(año_target, mes_target)

            # Rango Date para Cuota (DateField)
            inicio_mes_d = datetime(año_target, mes_target, 1).date()  # noqa: DTZ001
            fin_mes_d = datetime(año_target, mes_target, ultimo_dia).date() # noqa: DTZ001

            # Rango DateTime con zona horaria para Caja (DateTimeField)
            inicio_mes_dt = timezone.make_aware(datetime(año_target, mes_target, 1, 0, 0, 0), tz_actual) # noqa: DTZ001
            fin_mes_dt = timezone.make_aware(datetime(año_target, mes_target, ultimo_dia, 23, 59, 59), tz_actual) # noqa: DTZ001

            # Cobros registrados en Cuotas
            total_cuotas = Cuota.objects.filter(
                esta_pagada=True,
                fecha_pago_real__range=(inicio_mes_d, fin_mes_d)
            ).aggregate(total=Sum('monto_total'))['total'] or Decimal('0.00')

            # Cobros registrados como ingresos en Caja
            total_caja = Caja.objects.filter(
                tipo='ingreso',
                fecha__range=(inicio_mes_dt, fin_mes_dt)
            ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')

            total_mes = max(total_cuotas, total_caja)

            tendencias_crecimiento.append({
                'name': MESES_ABR[mes_target],
                'ingresos': float(total_mes)
            })

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
            },
            "tendencias_crecimiento": tendencias_crecimiento,
            "ultima_actualizacion": timezone.localtime().strftime('%H:%M:%S')
        })