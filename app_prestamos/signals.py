from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Prestamo, Cuota, Caja, HistorialCuota 

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
    # Verificamos si la cuota se marcó como pagada
    if instance.esta_pagada:
        descripcion_pago = f"Pago Cuota #{instance.numero_cuota} - Préstamo #{instance.prestamo.id}"
        
        # Evitamos duplicados en caja
        if not Caja.objects.filter(concepto=descripcion_pago).exists():
            # Calculamos la mora que se debió cobrar
            mora = instance.calcular_mora()
            # El monto real que entra a caja es el total de la cuota + la mora
            monto_total_recibido = instance.monto_total + mora
            
            Caja.objects.create(
                tipo='ingreso',
                monto=monto_total_recibido,
                concepto=descripcion_pago
            )


""" @receiver(pre_save, sender=Cuota)
def auditar_cambio_cuota(sender, instance, **kwargs):
    # Si la cuota ya existe (no es una creación nueva)
    if instance.pk:
        try:
            # Obtenemos la cuota como está grabada actualmente en la DB
            cuota_previa = Cuota.objects.get(pk=instance.pk)
            
            # Comparamos el estado de 'esta_pagada'
            if cuota_previa.esta_pagada != instance.esta_pagada:
                estado_viejo = "Pagada" if cuota_previa.esta_pagada else "Pendiente"
                estado_nuevo = "Pagada" if instance.esta_pagada else "Pendiente"
                
                # Guardamos el registro en el historial
                HistorialCuota.objects.create(
                    cuota=instance,
                    estado_anterior=estado_viejo,
                    estado_nuevo=estado_nuevo,
                    observaciones="Cambio detectado automáticamente por el sistema."
                )
        except Cuota.DoesNotExist:
            pass """