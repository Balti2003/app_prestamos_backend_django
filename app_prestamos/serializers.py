from rest_framework import serializers
from .models import Cliente, Prestamo, Cuota

class ClienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = '__all__'

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

    class Meta:
        model = Prestamo
        fields = '__all__'