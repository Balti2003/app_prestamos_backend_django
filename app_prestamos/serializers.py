from django.contrib.auth.password_validation import validate_password
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from .models import Caja, CajaDiaria, Cliente, Cuota, GarantiaCliente, Prestamo


class ClienteSerializer(serializers.ModelSerializer):
    prestamos_activos = serializers.SerializerMethodField()
    tiene_mora = serializers.SerializerMethodField()
    
    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'apellido', 'dni', 'telefono', 'direccion', 'tiene_mora', 'prestamos_activos']  # noqa: RUF012
    
    def get_tiene_mora(self, obj):
        return obj.prestamos.filter(cuotas__esta_pagada=False, cuotas__fecha_vencimiento__lt=timezone.localdate()).exists()

    def get_prestamos_activos(self, obj):
        prestamos = obj.prestamos.filter(estado__in=['activo', 'mora'], activo=True)
        return PrestamoMiniSerializer(prestamos, many=True).data


class ClienteResumenSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = ['id', 'nombre', 'apellido', 'dni']  # noqa: RUF012


class GarantiaClienteSerializer(serializers.ModelSerializer):
    es_imagen = serializers.ReadOnlyField()
    archivo_url = serializers.SerializerMethodField()

    class Meta:
        model = GarantiaCliente
        fields = ['id', 'cliente', 'titulo', 'archivo', 'archivo_url', 'es_imagen', 'fecha_subida']  # noqa: RUF012

    def get_archivo_url(self, obj):
        request = self.context.get('request')
        if obj.archivo and hasattr(obj.archivo, 'url'):
            if request:
                return request.build_absolute_uri(obj.archivo.url)
            return obj.archivo.url
        return None
    

class CuotaSerializer(serializers.ModelSerializer):
    total_con_mora = serializers.ReadOnlyField()
    saldo_pendiente = serializers.ReadOnlyField()
    es_parcial = serializers.ReadOnlyField()
    mora_actual = serializers.SerializerMethodField()

    class Meta:
        model = Cuota
        fields = '__all__'
    
    def get_mora_actual(self, obj):
        return obj.calcular_mora()


class PrestamoSerializer(serializers.ModelSerializer):
    # Usamos try/except o fallback en caso de que related_name difiera
    plan_pagos = serializers.SerializerMethodField()
    cliente_nombre = serializers.ReadOnlyField(source='cliente.apellido')
    cliente_detail = ClienteResumenSerializer(source='cliente', read_only=True)
    cuotas_pagadas_count = serializers.SerializerMethodField()
    cantidad_cuotas = serializers.ReadOnlyField(source='cuotas_totales')
    monto_cuota = serializers.SerializerMethodField()
    
    fecha_inicio = serializers.DateField(
        format="%Y-%m-%d", 
        input_formats=['%Y-%m-%d', 'iso-8601'],
        required=False, 
        allow_null=True
    )

    class Meta:
        model = Prestamo
        fields = '__all__'

    def get_monto_cuota(self, obj):
        # Toma el monto de la primera cuota registrada en el plan
        cuota = self._get_cuotas_qs(obj).first()
        return float(cuota.monto_total) if cuota else 0.0
    
    def _get_cuotas_qs(self, obj):
        if hasattr(obj, 'cuotas'):
            return obj.cuotas.all()
        return obj.cuota_set.all()

    def get_plan_pagos(self, obj):
        cuotas = self._get_cuotas_qs(obj)
        return CuotaSerializer(cuotas, many=True).data

    def get_cuotas_pagadas_count(self, obj):
        cuotas = self._get_cuotas_qs(obj)
        return cuotas.filter(esta_pagada=True).count()


class PrestamoMiniSerializer(serializers.ModelSerializer):
    """Serializer para mostrar deudas dentro del perfil del cliente"""
    cuotas_pagadas = serializers.SerializerMethodField()
    monto_cuota = serializers.SerializerMethodField()
    plan_pagos = CuotaSerializer(source='cuotas', many=True, read_only=True)

    class Meta:
        model = Prestamo
        fields = [  # noqa: RUF012
            'id', 
            'monto_solicitado', 
            'cuotas_totales', 
            'cuotas_pagadas', 
            'monto_cuota', 
            'estado',
            'plan_pagos'
        ]

    def get_cuotas_pagadas(self, obj):
        return obj.cuotas.filter(esta_pagada=True).count()

    def get_monto_cuota(self, obj):
        cuota = obj.cuotas.first()
        return cuota.monto_total if cuota else 0


class CajaSerializer(serializers.ModelSerializer):
    fecha_formateada = serializers.SerializerMethodField()
    cuota_id = serializers.SerializerMethodField()
    prestamo_id = serializers.SerializerMethodField()

    class Meta:
        model = Caja
        fields = ['id', 'tipo', 'monto', 'concepto', 'fecha', 'fecha_formateada', 'cuota_id', 'prestamo_id']  # noqa: RUF012
        
    def get_fecha_formateada(self, obj):
        if obj.fecha:
            fecha_local = timezone.localtime(obj.fecha)
            return fecha_local.strftime('%d/%m/%Y %H:%M')
        return "---"

    def get_cuota_id(self, obj):
        return obj.cuota.id if obj.cuota else None

    def get_prestamo_id(self, obj):
        return obj.prestamo.id if obj.prestamo else None


class HistorialPagosSerializer(serializers.ModelSerializer):
    """Serializer para listar el historial cronológico de pagos del cliente"""
    prestamo_id = serializers.ReadOnlyField(source='prestamo.id')
    
    class Meta:
        model = Cuota
        fields = ['id', 'prestamo_id', 'numero_cuota', 'monto_total', 'mora_pagada', 'fecha_pago_real']  # noqa: RUF012


class ClientePerfilSerializer(serializers.ModelSerializer):
    garantias = GarantiaClienteSerializer(many=True, read_only=True)
    prestamos_activos = serializers.SerializerMethodField()
    metricas_comportamiento = serializers.SerializerMethodField()
    historial_pagos = serializers.SerializerMethodField()

    class Meta:
        model = Cliente
        fields = [  # noqa: RUF012
            'id', 'nombre', 'apellido', 'dni', 'telefono', 'direccion', 
            'prestamos_activos', 'metricas_comportamiento', 'historial_pagos', 'garantias'
        ]

    def get_prestamos_activos(self, obj):
        prestamos = obj.prestamos.filter(estado__in=['activo', 'mora'], activo=True)
        return PrestamoMiniSerializer(prestamos, many=True).data

    def get_metricas_comportamiento(self, obj):
        cuotas_pagadas = Cuota.objects.filter(prestamo__cliente=obj, esta_pagada=True)
        
        total_pagadas = cuotas_pagadas.count()
        pagadas_con_mora = cuotas_pagadas.filter(mora_pagada__gt=0).count()
        pagadas_a_tiempo = total_pagadas - pagadas_con_mora

        ganancias = cuotas_pagadas.aggregate(
            intereses=Sum('monto_interes'),
            mora=Sum('mora_pagada')
        )
        total_ganancia = (ganancias['intereses'] or 0) + (ganancias['mora'] or 0)

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
        validate_password(value, user=self.context['request'].user)
        return value


class CajaDiariaSerializer(serializers.ModelSerializer):
    operador_apertura_nombre = serializers.ReadOnlyField(source='operador_apertura.username')
    operador_cierre_nombre = serializers.ReadOnlyField(source='operador_cierre.username')

    class Meta:
        model = CajaDiaria
        fields = '__all__'