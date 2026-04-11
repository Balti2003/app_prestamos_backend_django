from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Prestamo, Cuota, Caja

@receiver(post_save, sender=Prestamo)
def registrar_desembolso(sender, instance, created, **kwargs):
    # Si el préstamo pasa a 'activo' y no tenía registro en caja, descontamos el capital
    if instance.estado == 'activo':
        existe_egreso = Caja.objects.filter(concepto__contains=f"Préstamo #{instance.id}").exists()
        if not existe_egreso:
            Caja.objects.create(
                tipo='egreso',
                monto=instance.monto_solicitado,
                concepto=f"Desembolso Préstamo #{instance.id} - Cliente: {instance.cliente.apellido}"
            )