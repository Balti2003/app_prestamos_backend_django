from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Caja, Prestamo


@receiver(post_save, sender=Prestamo)
def registrar_egreso_prestamo(sender, instance, created, **kwargs):
    if created:
        # Se genera el movimiento de salida cuando nace el préstamo
        Caja.objects.create(
            tipo='egreso',
            monto=instance.monto_solicitado,
            concepto=f"Desembolso préstamo #{instance.id} - Cliente: {instance.cliente.nombre}"
        )