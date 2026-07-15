from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.db.models import Sum
from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


class Cliente(models.Model):
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    dni = models.CharField(max_length=20, unique=True)
    direccion = models.CharField(max_length=255, blank=True)
    telefono = models.CharField(max_length=20)
    score_interno = models.IntegerField(default=50) # 0 a 100
    creado_el = models.DateTimeField(auto_now_add=True)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.apellido}, {self.nombre}"
    
    def delete(self, *args, **kwargs ):
        self.activo = False
        self.save()
        

class Prestamo(models.Model):
    FRECUENCIAS = (
        ('diario', 'Diario'),
        ('semanal', 'Semanal'),
        ('mensual', 'Mensual'),
    )
    ESTADOS = (
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
    estado = models.CharField(max_length=15, choices=ESTADOS, default='activo')
    activo = models.BooleanField(default=True)
    
    def delete(self, *args, **kwargs ):
        self.activo = False
        self.save()
    
    def generar_plan_pagos(self):
        """
        Calcula y crea las cuotas usando Interés Directo con frecuencia dinámica.
        """
        interes_total_monetario = self.monto_solicitado * (self.tasa_interes / Decimal('100'))
        monto_total_a_pagar = self.monto_solicitado + interes_total_monetario
        
        monto_cuota = monto_total_a_pagar / self.cuotas_totales
        capital_por_cuota = self.monto_solicitado / self.cuotas_totales
        interes_por_cuota = interes_total_monetario / self.cuotas_totales

        # Normalizamos la frecuencia para evitar errores de mayúsculas/minúsculas
        frec_aux = self.frecuencia.lower() if self.frecuencia else 'mensual'

        for i in range(1, self.cuotas_totales + 1):
            if frec_aux == 'diario':
                fecha_venc = self.fecha_inicio + relativedelta(days=i)
            elif frec_aux == 'semanal':
                fecha_venc = self.fecha_inicio + relativedelta(weeks=i)
            elif frec_aux == 'quincenal':
                fecha_venc = self.fecha_inicio + relativedelta(days=i*15)
            else: # mensual o cualquier otro caso
                fecha_venc = self.fecha_inicio + relativedelta(months=i)

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
        """
        Controla si el préstamo llegó a su fin. 
        Se ejecuta de manera automática inmediatamente después de registrar un pago exacto.
        """
        # Contamos directamente cuántas cuotas quedan sin abonar utilizando la relación inversa corregida
        cuotas_pendientes = self.cuotas.filter(esta_pagada=False).count()
        
        if cuotas_pendientes == 0:
            self.estado = 'finalizado'
            self.save()
            print(f"--> [SISTEMA] Préstamo #{self.id} completado. Estado actualizado a FINALIZADO.")
        else:
            # Si todavía le quedan cuotas por delante, recalculamos si el legajo sigue en mora o vuelve a activo
            if hasattr(self, 'actualizar_estado_mora'):
                self.actualizar_estado_mora()
            
    def save(self, *args, **kwargs):
        if self.pk: # Si el préstamo ya existe (es una actualización)
            original = Prestamo.objects.get(pk=self.pk)
            if original.estado != self.estado:
                pass 
        super().save(*args, **kwargs)
    
    def actualizar_estado_mora(self):
        """
        Revisa si el préstamo tiene cuotas vencidas no pagadas.
        Si encuentra al menos una, cambia el estado a 'mora'.
        """
        hoy = timezone.localdate()
        
        # Buscamos si hay alguna cuota cuya fecha de vencimiento ya pasó y no está pagada
        cuotas_vencidas = self.cuotas.filter(
            fecha_vencimiento__lt=hoy, 
            esta_pagada=False
        ).exists()

        if cuotas_vencidas:
            if self.estado != 'mora':
                self.estado = 'mora'
                self.save()
                return True
        else:
            # Si no hay vencidas, pero estaba en mora (porque quizás se puso al día)
            # lo devolvemos a 'activo'
            if self.estado == 'mora':
                self.estado = 'activo'
                self.save()
                return True
        return False
        
    def __str__(self):
        return f"Préstamo #{self.id} - {self.cliente.apellido}"


class Cuota(models.Model):
    prestamo = models.ForeignKey(Prestamo, on_delete=models.CASCADE, related_name='cuotas')
    numero_cuota = models.PositiveIntegerField()
    monto_capital = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_interes = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fecha_vencimiento = models.DateField()
    fecha_pago_real = models.DateField(null=True, blank=True)
    esta_pagada = models.BooleanField(default=False)
    mora_pagada = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

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


class HistorialCuota(models.Model):
    cuota = models.ForeignKey(Cuota, on_delete=models.CASCADE, related_name='historial')
    estado_anterior = models.CharField(max_length=50)
    estado_nuevo = models.CharField(max_length=50)
    fecha_cambio = models.DateTimeField(auto_now_add=True)
    usuario = models.CharField(max_length=100, blank=True, null=True) # Por ahora texto, luego puede ser el User de Django
    observaciones = models.TextField(blank=True)

    def __str__(self):
        return f"Cuota {self.cuota.id}: {self.estado_anterior} -> {self.estado_nuevo}"


class Caja(models.Model):
    TIPOS = (
        ('ingreso', 'Ingreso (Cobro, Aporte)'),
        ('egreso', 'Egreso (Préstamo, Gasto)'),
    )
    tipo = models.CharField(max_length=10, choices=TIPOS)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    concepto = models.CharField(max_length=255)
    fecha = models.DateTimeField(auto_now_add=True)
    cuota = models.ForeignKey('Cuota', on_delete=models.SET_NULL, null=True, blank=True)
    caja_diaria = models.ForeignKey(
        'CajaDiaria', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='movimientos'
    )

    @classmethod
    def saldo_actual(cls):
        ingresos = cls.objects.filter(tipo='ingreso').aggregate(total=Sum('monto'))['total'] or 0
        egresos = cls.objects.filter(tipo='egreso').aggregate(total=Sum('monto'))['total'] or 0
        return ingresos - egresos
    
    def save(self, *args, **kwargs):
        # Si es un registro nuevo (no una actualización)
        if not self.pk:
            # Buscamos la caja diaria que esté abierta hoy
            caja_activa = CajaDiaria.objects.filter(estado='ABIERTA').first()
            
            if not caja_activa:
                raise ValidationError("No se puede registrar este movimiento porque no hay ninguna Caja Diaria abierta hoy.")
            
            # Asociamos automáticamente este movimiento a la caja activa
            self.caja_diaria = caja_activa
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tipo.upper()} - {self.monto} ({self.fecha.strftime('%d/%m/%Y')})" 


class HistorialEstado(models.Model):
    prestamo = models.ForeignKey(Prestamo, on_delete=models.CASCADE, related_name='historial_estados')
    estado_anterior = models.CharField(max_length=15)
    estado_nuevo = models.CharField(max_length=15)
    fecha_cambio = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.prestamo} cambió a {self.estado_nuevo} el {self.fecha_cambio}"


class CajaDiaria(models.Model):
    ESTADO_CHOICES = [
        ('ABIERTA', 'Abierta'),
        ('CERRADA', 'Cerrada'),
    ]

    fecha = models.DateField(auto_now_add=True, unique=True, verbose_name="Fecha de Operación")
    operador_apertura = models.ForeignKey(User, on_delete=models.PROTECT, related_name='cajas_abiertas')
    operador_cierre = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='cajas_cerradas')
    
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    
    # Valores Monetarios (Decimal para evitar problemas de redondeo de punto flotante)
    saldo_apertura = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    ingresos_sistema = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    egresos_sistema = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    saldo_estimado = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    saldo_real_fisico = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    diferencia = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='ABIERTA')
    observaciones = models.TextField(blank=True, null=True, help_text="Comentarios en caso de que haya diferencias")

    class Meta:
        ordering = ['-fecha']
        verbose_name = "Caja Diaria"
        verbose_name_plural = "Cajas Diarias"

    def __str__(self):
        return f"Caja {self.fecha} ({self.estado})"

    def clean(self):
        # Regla estricta: No permitir más de una caja abierta al mismo tiempo
        if self.estado == 'ABIERTA':
            cajas_abiertas = CajaDiaria.objects.filter(estado='ABIERTA').exclude(id=self.id)
            if cajas_abiertas.exists():
                raise ValidationError("Ya existe una caja abierta en el sistema. Debes cerrarla antes de iniciar una nueva.")