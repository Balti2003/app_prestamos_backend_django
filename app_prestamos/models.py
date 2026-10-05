import os
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Empresa(models.Model):
    nombre = models.CharField(max_length=150)
    cuit_rut = models.CharField(max_length=50, blank=True, null=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


class PerfilUsuario(models.Model):
    ROLES = (
        ('admin', 'Administrador'),
        ('operador', 'Operador'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='usuarios')
    rol = models.CharField(max_length=20, choices=ROLES, default='operador')

    def __str__(self):
        return f"{self.user.username} ({self.empresa.nombre}) - {self.rol}"


class PermisosOperador(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='permisos_custom')
    puede_crear_prestamo = models.BooleanField(default=True)
    puede_cobrar_cuota = models.BooleanField(default=True)
    puede_crear_cliente = models.BooleanField(default=True)
    puede_editar_cliente = models.BooleanField(default=False)
    puede_eliminar_cliente = models.BooleanField(default=False)
    puede_ver_caja = models.BooleanField(default=False)
    puede_ver_metricas = models.BooleanField(default=False)

    def __str__(self):
        return f"Permisos de {self.user.username}"


class Cliente(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='clientes')
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    dni = models.CharField(max_length=20)
    direccion = models.CharField(max_length=255, blank=True)
    telefono = models.CharField(max_length=20)
    score_interno = models.IntegerField(default=50)  # 0 a 100
    creado_el = models.DateTimeField(auto_now_add=True)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = ('empresa', 'dni')

    def __str__(self):
        return f"{self.apellido}, {self.nombre} ({self.empresa.nombre})"

    def delete(self, *args, **kwargs):
        self.activo = False
        self.save()


def cliente_garantia_path(instance, filename):
    return f'garantias/empresa_{instance.cliente.empresa_id}/cliente_{instance.cliente.dni}/{filename}'


class GarantiaCliente(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='garantias')
    titulo = models.CharField(max_length=150, help_text="Ej: Recibo de sueldo, Título del auto, Fotos propiedad")
    archivo = models.FileField(upload_to=cliente_garantia_path)
    fecha_subida = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.titulo} - {self.cliente.nombre} {self.cliente.apellido}"

    @property
    def es_imagen(self):
        ext = os.path.splitext(self.archivo.name)[1].lower()
        return ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']


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

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='prestamos')
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='prestamos')
    monto_solicitado = models.DecimalField(max_digits=12, decimal_places=2)
    tasa_interes = models.DecimalField(max_digits=5, decimal_places=2)
    cuotas_totales = models.PositiveIntegerField()
    frecuencia = models.CharField(max_length=10, choices=FRECUENCIAS, default='mensual')
    fecha_inicio = models.DateField(default=timezone.now, blank=True, null=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default='activo')
    activo = models.BooleanField(default=True)
    metodo_pago = models.CharField(
        max_length=50,
        default='efectivo',
        help_text="Forma de pago pactada para el préstamo"
    )

    def save(self, *args, **kwargs):
        # Hereda automáticamente la empresa del cliente
        if self.cliente and not self.empresa_id:
            self.empresa = self.cliente.empresa
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.activo = False
        self.save()

    def generar_plan_pagos(self):
        interes_total_monetario = self.monto_solicitado * (self.tasa_interes / Decimal(100))
        monto_total_a_pagar = self.monto_solicitado + interes_total_monetario

        monto_cuota = monto_total_a_pagar / self.cuotas_totales
        capital_por_cuota = self.monto_solicitado / self.cuotas_totales
        interes_por_cuota = interes_total_monetario / self.cuotas_totales

        frec_aux = self.frecuencia.lower() if self.frecuencia else 'mensual'

        for i in range(1, self.cuotas_totales + 1):
            if frec_aux == 'diario':
                fecha_venc = self.fecha_inicio + relativedelta(days=i)
            elif frec_aux == 'semanal':
                fecha_venc = self.fecha_inicio + relativedelta(weeks=i)
            elif frec_aux == 'quincenal':
                fecha_venc = self.fecha_inicio + relativedelta(days=i * 15)
            else:
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
        return self.cuotas.filter(esta_pagada=False).aggregate(models.Sum('monto_total'))['monto_total__sum'] or Decimal('0.00')

    def check_finalizacion(self):
        cuotas_pendientes = self.cuotas.filter(esta_pagada=False).count()
        if cuotas_pendientes == 0:
            self.estado = 'finalizado'
            self.save()
        else:
            self.actualizar_estado_mora()

    def actualizar_estado_mora(self):
        hoy = timezone.localdate()
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
            if self.estado == 'mora':
                self.estado = 'activo'
                self.save()
                return True
        return False

    def __str__(self):
        return f"Préstamo #{self.id} - {self.cliente.apellido} ({self.empresa.nombre})"


METODOS_PAGO = [
    ('efectivo', 'Efectivo'),
    ('transferencia', 'Transferencia'),
    ('otro', 'Otro'),
]


class Cuota(models.Model):
    prestamo = models.ForeignKey(Prestamo, on_delete=models.CASCADE, related_name='cuotas')
    numero_cuota = models.PositiveIntegerField()
    monto_capital = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_interes = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_pagado = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    fecha_vencimiento = models.DateField()
    fecha_pago_real = models.DateField(null=True, blank=True)
    esta_pagada = models.BooleanField(default=False)
    mora_pagada = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    metodo_pago = models.CharField(
        max_length=20,
        choices=METODOS_PAGO,
        default='efectivo'
    )

    def __str__(self):
        return f"Cuota {self.numero_cuota} de {self.prestamo}"

    @property
    def saldo_pendiente(self):
        if self.esta_pagada:
            return Decimal('0.00')
        saldo = self.monto_total - self.monto_pagado
        return max(Decimal('0.00'), saldo)

    @property
    def es_parcial(self):
        return self.monto_pagado > 0 and not self.esta_pagada

    def calcular_mora(self, tasa_mora_diaria=Decimal('0.5')):
        if not self.esta_pagada and timezone.now().date() > self.fecha_vencimiento:
            dias_atraso = (timezone.now().date() - self.fecha_vencimiento).days
            mora_generada = self.saldo_pendiente * (tasa_mora_diaria / Decimal(100)) * dias_atraso
            mora_generada = mora_generada.quantize(Decimal('0.01'))
            mora_restante = mora_generada - self.mora_pagada
            return max(Decimal('0.00'), mora_restante)
        return Decimal('0.00')

    @property
    def total_con_mora(self):
        return self.saldo_pendiente + self.calcular_mora()


class HistorialCuota(models.Model):
    cuota = models.ForeignKey(Cuota, on_delete=models.CASCADE, related_name='historial')
    estado_anterior = models.CharField(max_length=50)
    estado_nuevo = models.CharField(max_length=50)
    fecha_cambio = models.DateTimeField(auto_now_add=True)
    usuario = models.CharField(max_length=100, blank=True, null=True)
    observaciones = models.TextField(blank=True)

    def __str__(self):
        return f"Cuota {self.cuota.id}: {self.estado_anterior} -> {self.estado_nuevo}"


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

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='cajas_diarias')
    fecha = models.DateField(auto_now_add=True, verbose_name="Fecha de Operación")
    operador_apertura = models.ForeignKey(User, on_delete=models.PROTECT, related_name='cajas_abiertas')
    operador_cierre = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='cajas_cerradas')

    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)

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
        unique_together = ('empresa', 'fecha')

    def __str__(self):
        return f"Caja {self.fecha} - {self.empresa.nombre} ({self.estado})"

    def clean(self):
        # Cada empresa solo puede tener 1 caja abierta en simultáneo
        if self.estado == 'ABIERTA':
            cajas_abiertas = CajaDiaria.objects.filter(
                empresa=self.empresa,
                estado='ABIERTA'
            ).exclude(id=self.id)
            if cajas_abiertas.exists():
                raise ValidationError(f"Ya existe una caja abierta para {self.empresa.nombre}. Debes cerrarla antes de iniciar una nueva.")


class Caja(models.Model):
    TIPOS = (
        ('ingreso', 'Ingreso (Cobro, Aporte)'),
        ('egreso', 'Egreso (Préstamo, Gasto)'),
    )
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='movimientos_caja')
    tipo = models.CharField(max_length=10, choices=TIPOS)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    concepto = models.CharField(max_length=255)
    fecha = models.DateTimeField(auto_now_add=True)
    prestamo = models.ForeignKey(Prestamo, on_delete=models.SET_NULL, null=True, blank=True)
    cuota = models.ForeignKey(Cuota, on_delete=models.SET_NULL, null=True, blank=True)
    caja_diaria = models.ForeignKey(
        CajaDiaria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos'
    )
    metodo_pago = models.CharField(
        max_length=20,
        choices=METODOS_PAGO,
        default='efectivo'
    )

    def save(self, *args, **kwargs):
        # Asignación automática de empresa según el préstamo o la cuota
        if not self.empresa_id:
            if self.prestamo:
                self.empresa = self.prestamo.empresa
            elif self.cuota:
                self.empresa = self.cuota.prestamo.empresa
            elif self.caja_diaria:
                self.empresa = self.caja_diaria.empresa

        # Valida que exista caja diaria abierta para esta empresa específica
        if not self.pk:
            if not self.empresa_id:
                raise ValidationError("No se puede registrar un movimiento de caja sin empresa asociada.")

            caja_activa = CajaDiaria.objects.filter(empresa=self.empresa, estado='ABIERTA').first()
            if not caja_activa:
                raise ValidationError(f"No hay ninguna Caja Diaria abierta hoy para {self.empresa.nombre}.")
            self.caja_diaria = caja_activa

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tipo.upper()} - {self.monto} ({self.fecha.strftime('%d/%m/%Y')}) - {self.empresa.nombre}"