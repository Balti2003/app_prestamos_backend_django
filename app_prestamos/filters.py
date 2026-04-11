import django_filters
from .models import Prestamo, Cuota

class PrestamoFilter(django_filters.FilterSet):
    # Filtro por rango de monto
    min_monto = django_filters.NumberFilter(field_name="monto_solicitado", lookup_expr='gte')
    max_monto = django_filters.NumberFilter(field_name="monto_solicitado", lookup_expr='lte')
    
    # Filtro por DNI del cliente (búsqueda exacta)
    dni = django_filters.CharFilter(field_name="cliente__dni")

    class Meta:
        model = Prestamo
        fields = ['estado', 'frecuencia', 'cliente']

class CuotaFilter(django_filters.FilterSet):
    # Filtro para ver cuotas que vencen en un rango de fechas
    vence_desde = django_filters.DateFilter(field_name="fecha_vencimiento", lookup_expr='gte')
    vence_hasta = django_filters.DateFilter(field_name="fecha_vencimiento", lookup_expr='lte')
    
    class Meta:
        model = Cuota
        fields = ['esta_pagada', 'prestamo']