from django.utils import timezone
from rest_framework import serializers
from .models import Cliente, Prestamo, Cuota, Caja

class ClienteSerializer(serializers.ModelSerializer):
    prestamos_activos = serializers.SerializerMethodField()
    tiene_mora = serializers.SerializerMethodField()
    
    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'apellido', 'dni', 'telefono', 'direccion', 'tiene_mora', 'prestamos_activos']
    
    def get_tiene_mora(self, obj):
        from django.utils import timezone
        return obj.prestamos.filter(cuotas__esta_pagada=False, cuotas__fecha_vencimiento__lt=timezone.localdate()).exists()

    def get_prestamos_activos(self, obj):
        prestamos = obj.prestamos.filter(estado__in=['activo', 'mora'], activo=True)
        return PrestamoMiniSerializer(prestamos, many=True).data


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
        if obj.fecha:
            # 1. Convertimos la hora UTC de la base de datos a la hora local configurada (America/Argentina/Cordoba)
            fecha_local = timezone.localtime(obj.fecha)
            # 2. La formateamos exactamente como la tenías en la tabla
            return fecha_local.strftime('%d/%m/%Y %H:%M')
        return "---"