from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Prestamo, Cuota, Caja

@receiver(post_save, sender=Prestamo)
def registrar_egreso_prestamo(sender, instance, created, **kwargs):
    if created:
        # Se genera el movimiento de salida cuando nace el préstamo
        Caja.objects.create(
            tipo='egreso',
            monto=instance.monto_solicitado,
            concepto=f"Desembolso préstamo #{instance.id} - Cliente: {instance.cliente.nombre}"
        )


@receiver(post_save, sender=Cuota)
def registrar_ingreso_cuota(sender, instance, created, **kwargs):
    # Solo registramos en caja si la cuota se marcó como pagada en esta actualización
    # y si el monto es mayor a 0 (para evitar registros vacíos)
    if instance.esta_pagada and instance.monto_pagado > 0:
        # Verificamos si ya existe un registro de caja para esta cuota para no duplicar
        # en caso de que alguien guarde la cuota dos veces.
        descripcion_pago = f"Pago Cuota #{instance.numero_cuota} - Préstamo #{instance.prestamo.id}"
        
        if not Caja.objects.filter(descripcion=descripcion_pago).exists():
            Caja.objects.create(
                tipo='ingreso',
                monto=instance.monto_pagado,
                concepto=descripcion_pago
            )