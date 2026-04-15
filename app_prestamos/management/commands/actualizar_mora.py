from django.core.management.base import BaseCommand
from django.utils import timezone
from app_prestamos.models import Cuota, HistorialEstado

class Command(BaseCommand):
    help = 'Revisa cuotas vencidas y actualiza el estado de los préstamos a MORA'

    def handle(self, *args, **options):
        hoy = timezone.now().date()
        
        # 1. Buscar cuotas que vencieron antes de hoy y no están pagas
        cuotas_vencidas = Cuota.objects.filter(
            esta_pagada=False, 
            fecha_vencimiento__lt=hoy,
            prestamo__estado='activo' # Solo préstamos que aún no están en mora
        )

        prestamos_a_mora = set([c.prestamo for c in cuotas_vencidas])

        for p in prestamos_a_mora:
            estado_viejo = p.estado
            p.estado = 'mora'
            p.save()

            # Registrar en el historial
            HistorialEstado.objects.create(
                prestamo=p,
                estado_anterior=estado_viejo,
                estado_nuevo='mora',
                motivo="Actualización automática por cuota vencida"
            )
            self.stdout.write(self.style.SUCCESS(f'Préstamo #{p.id} pasado a MORA'))

        if not prestamos_a_mora:
            self.stdout.write(self.style.WARNING('No se encontraron nuevos préstamos en mora.'))