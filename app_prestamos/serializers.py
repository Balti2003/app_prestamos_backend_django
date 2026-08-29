from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from .models import Caja, CajaDiaria, Cliente, Cuota, GarantiaCliente, Prestamo

DIAS_SEMANA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

def calcular_estado_financiero_cliente(cliente_obj):
    """
    Calcula el estado del semáforo, la frecuencia del préstamo activo y
    las fechas exactas con día de la semana (inicio y próximo vencimiento).
    """
    hoy = timezone.localdate()
    
    # Obtenemos las cuotas impagas del préstamo activo ordenadas por vencimiento
    cuotas_pendientes = Cuota.objects.filter(
        prestamo__cliente=cliente_obj,
        prestamo__activo=True,
        esta_pagada=False
    ).select_related('prestamo').order_by('fecha_vencimiento')

    if not cuotas_pendientes.exists():
        return {
            'estado': 'sin_deuda',
            'label': 'SIN DEUDA',
            'dias_restantes': None,
            'frecuencia': None,
            'proximo_vencimiento_texto': None,
            'fecha_inicio_texto': None,
        }

    primera_cuota = cuotas_pendientes.first()
    prestamo = primera_cuota.prestamo
    fecha_venc = primera_cuota.fecha_vencimiento
    fecha_ini = prestamo.fecha_inicio
    dias_diferencia = (fecha_venc - hoy).days

    # Formateo con día de la semana en español (Ej: "Martes 08/09")
    dia_venc_nombre = DIAS_SEMANA[fecha_venc.weekday()]
    vencimiento_formateado = f"{dia_venc_nombre} {fecha_venc.strftime('%d/%m')}"

    dia_ini_nombre = DIAS_SEMANA[fecha_ini.weekday()] if fecha_ini else None
    inicio_formateado = f"{dia_ini_nombre} {fecha_ini.strftime('%d/%m')}" if fecha_ini else None

    # Determinamos el estado del semáforo con los textos limpios
    if dias_diferencia < 0:
        dias_abs = abs(dias_diferencia)
        estado = 'moroso'
        label = f"PAGO ATRASADO ({dias_abs} {'DÍA' if dias_abs == 1 else 'DÍAS'})"
    elif 0 <= dias_diferencia <= 2:
        estado = 'por_vencer'
        if dias_diferencia == 0:
            label = "VENCE HOY"
        elif dias_diferencia == 1:
            label = "FALTA 1 DÍA"
        else:
            label = f"FALTAN {dias_diferencia} DÍAS"
    else:
        estado = 'al_dia'
        label = 'AL DÍA'

    return {
        'estado': estado,
        'label': label,
        'dias_restantes': dias_diferencia,
        'frecuencia': prestamo.frecuencia,  # 'diario', 'semanal', 'quincenal', 'mensual'
        'proximo_vencimiento_texto': vencimiento_formateado,
        'fecha_inicio_texto': inicio_formateado,
        'numero_cuota_pendiente': primera_cuota.numero_cuota,
        'cuotas_totales': prestamo.cuotas_totales
    }


class ClienteSerializer(serializers.ModelSerializer):
    prestamos_activos = serializers.SerializerMethodField()
    tiene_mora = serializers.SerializerMethodField()
    estado_financiero = serializers.SerializerMethodField()

    class Meta:
        model = Cliente
        fields = [  # noqa: RUF012
            'id', 
            'nombre', 
            'apellido', 
            'dni', 
            'telefono', 
            'direccion', 
            'tiene_mora', 
            'estado_financiero', 
            'prestamos_activos'
        ]

    def get_tiene_mora(self, obj):
        return obj.prestamos.filter(
            activo=True,
            cuotas__esta_pagada=False, 
            cuotas__fecha_vencimiento__lt=timezone.localdate()
        ).exists()

    def get_prestamos_activos(self, obj):
        prestamos = obj.prestamos.filter(estado__in=['activo', 'mora'], activo=True)
        return PrestamoMiniSerializer(prestamos, many=True).data

    def get_estado_financiero(self, obj):
        return calcular_estado_financiero_cliente(obj)


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
    plan_pagos = serializers.SerializerMethodField()
    cliente_nombre = serializers.ReadOnlyField(source='cliente.apellido')
    cliente_detail = ClienteResumenSerializer(source='cliente', read_only=True)
    cuotas_pagadas_count = serializers.SerializerMethodField()
    cantidad_cuotas = serializers.IntegerField(source='cuotas_totales', required=False)
    monto_cuota = serializers.SerializerMethodField()
    metodo_pago_detalle = serializers.CharField(write_only=True, required=False, allow_blank=True)
    
    fecha_inicio = serializers.DateField(
        format="%Y-%m-%d", 
        input_formats=['%Y-%m-%d', 'iso-8601'],
        required=False, 
        allow_null=True
    )

    class Meta:
        model = Prestamo
        fields = '__all__'
        extra_kwargs = {  # noqa: RUF012
            'cuotas_totales': {'required': False},
            'estado': {'required': False},
            'activo': {'required': False},
            'monto_total': {'required': False},
        }

    def to_internal_value(self, data):
        data_dict = data.copy() if hasattr(data, 'copy') else dict(data)
        if 'cantidad_cuotas' in data_dict and 'cuotas_totales' not in data_dict:
            data_dict['cuotas_totales'] = data_dict['cantidad_cuotas']
        return super().to_internal_value(data_dict)

    def create(self, validated_data):
        metodo_pago_detalle = validated_data.pop('metodo_pago_detalle', '').strip()
        metodo_pago_raw = validated_data.get('metodo_pago', 'efectivo')

        if str(metodo_pago_raw).lower() == 'otro' and metodo_pago_detalle:
            validated_data['metodo_pago'] = metodo_pago_detalle

        return super().create(validated_data)

    def get_monto_cuota(self, obj):
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
            'metodo_pago',
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
        fields = ['id', 'tipo', 'monto', 'concepto', 'fecha', 'fecha_formateada', 'metodo_pago', 'cuota_id', 'prestamo_id']  # noqa: RUF012
        
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
        fields = ['id', 'prestamo_id', 'numero_cuota', 'monto_total', 'mora_pagada', 'fecha_pago_real', 'metodo_pago']  # noqa: RUF012


class ClientePerfilSerializer(serializers.ModelSerializer):
    garantias = GarantiaClienteSerializer(many=True, read_only=True)
    prestamos_activos = serializers.SerializerMethodField()
    metricas_comportamiento = serializers.SerializerMethodField()
    historial_pagos = serializers.SerializerMethodField()
    estado_financiero = serializers.SerializerMethodField()
    tiene_mora = serializers.SerializerMethodField()

    class Meta:
        model = Cliente
        fields = [  # noqa: RUF012
            'id', 'nombre', 'apellido', 'dni', 'telefono', 'direccion', 
            'tiene_mora', 'estado_financiero',
            'prestamos_activos', 'metricas_comportamiento', 'historial_pagos', 'garantias'
        ]

    def get_tiene_mora(self, obj):
        return obj.prestamos.filter(
            activo=True,
            cuotas__esta_pagada=False, 
            cuotas__fecha_vencimiento__lt=timezone.localdate()
        ).exists()

    def get_estado_financiero(self, obj):
        return calcular_estado_financiero_cliente(obj)

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


class UserSerializer(serializers.ModelSerializer):
    """Serializer para devolver el usuario autenticado y su rol al frontend"""
    es_admin = serializers.BooleanField(source='is_staff')

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'email', 'es_admin']  # noqa: RUF012