from rest_framework import serializers
from .models import Cliente, Prestamo, Cuota, Caja

class ClienteSerializer(serializers.ModelSerializer):
    tiene_mora = serializers.SerializerMethodField()
    
    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'apellido', 'dni', 'telefono', 'direccion', 'tiene_mora']
    
    def get_tiene_mora(self, obj):
        # Buscamos si el cliente tiene al menos una cuota vencida sin pagar
        from .models import Cuota
        from django.utils import timezone
        return Cuota.objects.filter(
            prestamo__cliente=obj,
            esta_pagada=False,
            fecha_vencimiento__lt=timezone.localdate()
        ).exists()


class CuotaSerializer(serializers.ModelSerializer):
    # Incluimos el cálculo de mora que definimos en el modelo
    total_con_mora = serializers.ReadOnlyField()
    mora_actual = serializers.SerializerMethodField()

    class Meta:
        model = Cuota
        fields = '__all__'
    
    def get_mora_actual(self, obj):
        return obj.calcular_mora()

class PrestamoSerializer(serializers.ModelSerializer):
    # Esto permite ver las cuotas dentro del detalle del préstamo
    plan_pagos = CuotaSerializer(many=True, read_only=True)
    cliente_nombre = serializers.ReadOnlyField(source='cliente.apellido')
    fecha_inicio = serializers.DateField(format="%Y-%m-%d", input_formats=['%Y-%m-%d', 'iso-8601'])

    class Meta:
        model = Prestamo
        fields = '__all__'

class PrestamoMiniSerializer(serializers.ModelSerializer):
    """Serializer para mostrar deudas dentro del perfil del cliente"""
    cuotas_pagadas = serializers.SerializerMethodField()
    monto_cuota = serializers.SerializerMethodField()

    class Meta:
        model = Prestamo
        fields = [
            'id', 
            'monto_solicitado', 
            'cuotas_totales', 
            'cuotas_pagadas', 
            'monto_cuota', 
            'estado'
        ]

    def get_cuotas_pagadas(self, obj):
        # Contamos cuántas cuotas tienen esta_pagada=True
        return obj.cuotas.filter(esta_pagada=True).count()

    def get_monto_cuota(self, obj):
        # Tomamos el monto_total de la primera cuota del plan
        cuota = obj.cuotas.first()
        return cuota.monto_total if cuota else 0

class CajaSerializer(serializers.ModelSerializer):
    fecha_formateada = serializers.SerializerMethodField()
    cuota_id = serializers.ReadOnlyField(source='cuota.id')

    class Meta:
        model = Caja
        fields = ['id', 'tipo', 'monto', 'concepto', 'fecha', 'fecha_formateada', 'cuota_id']
    
    def get_fecha_formateada(self, obj):
        # Formateo para que sea legible en el frontend
        return obj.fecha.strftime("%d/%m/%Y %H:%M")