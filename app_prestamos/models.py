from decimal import Decimal
from django.db import models
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum

        
class Cliente(models.Model):
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    dni = models.CharField(max_length=20, unique=True)
    direccion = models.CharField(max_length=255, blank=True)
    telefono = models.CharField(max_length=20)
    score_interno = models.IntegerField(default=50) # 0 a 100
    creado_el = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.apellido}, {self.nombre}"


class Prestamo(models.Model):
    FRECUENCIAS = (
        ('diario', 'Diario'),
        ('semanal', 'Semanal'),
        ('mensual', 'Mensual'),
    )
    ESTADOS = (
        ('pendiente', 'Pendiente de Aprobación'),
        ('activo', 'Activo'),
        ('mora', 'En Mora'),
        ('finalizado', 'Finalizado'),
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='prestamos')
    monto_solicitado = models.DecimalField(max_digits=12, decimal_places=2)
    tasa_interes = models.DecimalField(max_digits=5, decimal_places=2) # Tasa periódica
    cuotas_totales = models.PositiveIntegerField()
    frecuencia = models.CharField(max_length=10, choices=FRECUENCIAS, default='mensual')
    fecha_inicio = models.DateField(default=timezone.now)
    estado = models.CharField(max_length=15, choices=ESTADOS, default='pendiente')

    def __str__(self):
        return f"Préstamo #{self.id} - {self.cliente.apellido}"
    
    def generar_plan_pagos(self):
        """
        Calcula y crea las cuotas usando Interés Directo.
        """
        # Interés total = Capital * (Tasa / 100)
        interes_total_monetario = self.monto_solicitado * (self.tasa_interes / Decimal('100'))
        monto_total_a_pagar = self.monto_solicitado + interes_total_monetario
        
        monto_cuota = monto_total_a_pagar / self.cuotas_totales
        capital_por_cuota = self.monto_solicitado / self.cuotas_totales
        interes_por_cuota = interes_total_monetario / self.cuotas_totales

        for i in range(1, self.cuotas_totales + 1):
            # Calcular fecha de vencimiento según frecuencia
            if self.frecuencia == 'diario':
                dias_a_sumar = i
            elif self.frecuencia == 'semanal':
                dias_a_sumar = i * 7
            else: # mensual
                dias_a_sumar = i * 30
            
            fecha_venc = self.fecha_inicio + timedelta(days=dias_a_sumar)

            Cuota.objects.create(
                prestamo=self,
                numero_cuota=i,
                monto_capital=capital_por_cuota,
                monto_interes=interes_por_cuota,
                monto_total=monto_cuota,
                fecha_vencimiento=fecha_venc
            )
    
    @property
    def saldo_pendiente(self):
        return self.plan_pagos.filter(esta_pagada=False).aggregate(models.Sum('monto_total'))['monto_total__sum'] or 0

    def check_finalizacion(self):
        if not self.plan_pagos.filter(esta_pagada=False).exists():
            self.estado = 'finalizado'
            self.save()


class Cuota(models.Model):
    prestamo = models.ForeignKey(Prestamo, on_delete=models.CASCADE, related_name='plan_pagos')
    numero_cuota = models.PositiveIntegerField()
    monto_capital = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_interes = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fecha_vencimiento = models.DateField()
    fecha_pago_real = models.DateField(null=True, blank=True)
    esta_pagada = models.BooleanField(default=False)

    def __str__(self):
        return f"Cuota {self.numero_cuota} de {self.prestamo}"
    
    def calcular_mora(self, tasa_mora_diaria=Decimal('0.5')): # Ejemplo 0.5% diario
        if not self.esta_pagada and timezone.now().date() > self.fecha_vencimiento:
            dias_atraso = (timezone.now().date() - self.fecha_vencimiento).days
            monto_mora = self.monto_total * (tasa_mora_diaria / Decimal('100')) * dias_atraso
            return monto_mora.quantize(Decimal('0.01'))
        return Decimal('0.00')

    @property
    def total_con_mora(self):
        return self.monto_total + self.calcular_mora()


class Caja(models.Model):
    TIPOS = (
        ('ingreso', 'Ingreso (Cobro, Aporte)'),
        ('egreso', 'Egreso (Préstamo, Gasto)'),
    )
    tipo = models.CharField(max_length=10, choices=TIPOS)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    concepto = models.CharField(max_length=255)
    fecha = models.DateTimeField(auto_now_add=True)

    @classmethod
    def saldo_actual(cls):
        ingresos = cls.objects.filter(tipo='ingreso').aggregate(total=Sum('monto'))['total'] or 0
        egresos = cls.objects.filter(tipo='egreso').aggregate(total=Sum('monto'))['total'] or 0
        return ingresos - egresos

    def __str__(self):
        return f"{self.tipo.upper()} - {self.monto} ({self.fecha.strftime('%d/%m/%Y')})"