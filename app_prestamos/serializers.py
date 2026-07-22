from django.utils import timezone
from rest_framework import serializers
from .models import Cliente, Prestamo, Cuota, Caja, GarantiaCliente
from django.db.models import Sum
from django.contrib.auth.password_validation import validate_password
from .models import CajaDiaria

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


class GarantiaClienteSerializer(serializers.ModelSerializer):
    es_imagen = serializers.ReadOnlyField()
    archivo_url = serializers.SerializerMethodField()

    class Meta:
        model = GarantiaCliente
        fields = ['id', 'cliente', 'titulo', 'archivo', 'archivo_url', 'es_imagen', 'fecha_subida']

    def get_archivo_url(self, obj):
        request = self.context.get('request')
        if obj.archivo and hasattr(obj.archivo, 'url'):
            if request:
                return request.build_absolute_uri(obj.archivo.url)
            return obj.archivo.url
        return None
    

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
    fecha_inicio = serializers.DateTimeField(
        format="%Y-%m-%d %H:%M", 
        input_formats=['%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d', 'iso-8601'],
        required=False, 
        allow_null=True
    )

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
    cuota_id = serializers.SerializerMethodField()
    prestamo_id = serializers.SerializerMethodField()

    class Meta:
        model = Caja
        fields = ['id', 'tipo', 'monto', 'concepto', 'fecha', 'fecha_formateada', 'cuota_id', 'prestamo_id']
        
    def get_fecha_formateada(self, obj):
        if obj.fecha:
            fecha_local = timezone.localtime(obj.fecha)
            return fecha_local.strftime('%d/%m/%Y %H:%M')
        return "---"

    # Protección contra valores nulos (None)
    def get_cuota_id(self, obj):
        return obj.cuota.id if obj.cuota else None

    def get_prestamo_id(self, obj):
        return obj.prestamo.id if obj.prestamo else None

class HistorialPagosSerializer(serializers.ModelSerializer):
    """Serializer para listar el historial cronológico de pagos del cliente"""
    prestamo_id = serializers.ReadOnlyField(source='prestamo.id')
    
    class Meta:
        model = Cuota
        fields = ['id', 'prestamo_id', 'numero_cuota', 'monto_total', 'mora_pagada', 'fecha_pago_real']

class ClientePerfilSerializer(serializers.ModelSerializer):
    garantias = GarantiaClienteSerializer(many=True, read_only=True)
    prestamos_activos = serializers.SerializerMethodField()
    metricas_comportamiento = serializers.SerializerMethodField()
    historial_pagos = serializers.SerializerMethodField()

    class Meta:
        model = Cliente
        fields = [
            'id', 'nombre', 'apellido', 'dni', 'telefono', 'direccion', 
            'prestamos_activos', 'metricas_comportamiento', 'historial_pagos', 'garantias'
        ]

    def get_prestamos_activos(self, obj):
        # Usamos el PrestamoMiniSerializer
        prestamos = obj.prestamos.filter(estado__in=['activo', 'mora'], activo=True)
        return PrestamoMiniSerializer(prestamos, many=True).data

    def get_metricas_comportamiento(self, obj):
        # Todas las cuotas pagadas históricas de este cliente
        cuotas_pagadas = Cuota.objects.filter(prestamo__cliente=obj, esta_pagada=True)
        
        total_pagadas = cuotas_pagadas.count()
        # Una cuota se pagó con mora si el campo mora_pagada es mayor a cero
        pagadas_con_mora = cuotas_pagadas.filter(mora_pagada__gt=0).count()
        pagadas_a_tiempo = total_pagadas - pagadas_con_mora

        # Calcular ganancias generadas por este cliente específico
        ganancias = cuotas_pagadas.aggregate(
            intereses=Sum('monto_interes'),
            mora=Sum('mora_pagada')
        )
        total_ganancia = (ganancias['intereses'] or 0) + (ganancias['mora'] or 0)

        # Calcular porcentaje de puntualidad
        tasa_puntualidad = (pagadas_a_tiempo / total_pagadas * 100) if total_pagadas > 0 else 100

        return {
            "total_prestamos": obj.prestamos.filter(activo=True).count(),
            "cuotas_pagadas_total": total_pagadas,
            "cuotas_a_tiempo": pagadas_a_tiempo,
            "cuotas_con_mora": pagadas_con_mora,
            "tasa_puntualidad_porcentaje": round(tasa_puntualidad, 1),
            "ganancia_generada": float(total_ganancia)
        }

    def get_historial_pagos(self, obj):
        # Traemos todos los pagos ordenados desde el más reciente al más viejo
        cuotas = Cuota.objects.filter(
            prestamo__cliente=obj, 
            esta_pagada=True
        ).order_by('-fecha_pago_real')
        return HistorialPagosSerializer(cuotas, many=True).data
    
class CambiarPasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("La contraseña actual es incorrecta.")
        return value

    def validate_new_password(self, value):
        # Valida que cumpla las políticas de seguridad de Django (longitud, caracteres, etc.)
        validate_password(value, user=self.context['request'].user)
        return value

class CajaDiariaSerializer(serializers.ModelSerializer):
    operador_apertura_nombre = serializers.ReadOnlyField(source='operador_apertura.username')
    operador_cierre_nombre = serializers.ReadOnlyField(source='operador_cierre.username')

    class Meta:
        model = CajaDiaria
        fields = '__all__'