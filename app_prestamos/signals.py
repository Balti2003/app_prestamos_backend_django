from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Caja, CajaDiaria, Prestamo


@receiver(post_save, sender=Prestamo)
def registrar_egreso_prestamo(sender, instance, created, **kwargs):
    if created:
        # Se genera el movimiento de salida cuando nace el préstamo
        Caja.objects.create(
            tipo='egreso',
            monto=instance.monto_solicitado,
            concepto=f"Desembolso préstamo #{instance.id} - Cliente: {instance.cliente.nombre}"
        )


@receiver(post_save, sender=Prestamo)
def reajustar_caja_por_desactivacion_prestamo(sender, instance, created, **kwargs):
    """
    Detecta si un préstamo existente fue desactivado (Soft Delete: activo=False)
    y devuelve el dinero a la Caja.
    """
    if not created and not instance.activo:
        # Verificamos que exista una caja diaria abierta para registrar la devolución
        caja_activa = CajaDiaria.objects.filter(estado='ABIERTA').first()
        if not caja_activa:
            return  # O puedes lanzar una excepción si prefieres bloquear la acción

        # Revisamos si ya devolvimos el dinero previamente para no duplicar el ingreso
        concepto_devolucion = f"Devolución por anulación de Préstamo #{instance.id}"
        ya_devuelto = Caja.objects.filter(
            prestamo=instance,
            concepto=concepto_devolucion
        ).exists()

        if not ya_devuelto:
            Caja.objects.create(
                tipo='ingreso',
                monto=instance.monto_solicitado,
                concepto=concepto_devolucion,
                prestamo=instance,
                metodo_pago='efectivo'
            )
            print(f"Se reingresaron ${instance.monto_solicitado} a la caja por anulación del Préstamo #{instance.id}.")


@receiver(post_delete, sender=Prestamo)
def reajustar_caja_por_borrado_fisico_prestamo(sender, instance, **kwargs):
    """
    Si el préstamo es eliminado FÍSICAMENTE de la base de datos (p. ej. desde el Django Admin),
    devuelve el dinero entregado a la Caja.
    """
    caja_activa = CajaDiaria.objects.filter(estado='ABIERTA').first()
    if not caja_activa:
        return

    Caja.objects.create(
        tipo='ingreso',
        monto=instance.monto_solicitado,
        concepto=f"Devolución por borrado físico de Préstamo #{instance.id}",
        metodo_pago='efectivo'
    )
    print(f"Se reingresaron ${instance.monto_solicitado} a la caja por eliminación del Préstamo #{instance.id}.")