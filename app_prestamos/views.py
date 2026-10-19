import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import mercadopago
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, parsers, serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from .filters import CajaFilter, CuotaFilter, PrestamoFilter
from .models import (
    Caja,
    CajaDiaria,
    Cliente,
    Cuota,
    Empresa,
    GarantiaCliente,
    HistorialCuota,
    PerfilUsuario,
    Prestamo,
)
from .serializers import (
    CajaDiariaSerializer,
    CajaSerializer,
    CambiarPasswordSerializer,
    ClientePerfilSerializer,
    ClienteResumenSerializer,
    ClienteSerializer,
    CrearOperadorSerializer,
    CuotaSerializer,
    GarantiaClienteSerializer,
    PrestamoSerializer,
    UserSerializer,
)
from .utils import generar_pdf_desembolso_seguro, generar_recibo_pago_pdf


def get_user_empresa(user):
    """Retorna la empresa asociada al usuario autenticado y verifica suscripción"""
    if hasattr(user, 'perfil') and user.perfil.empresa:
        empresa = user.perfil.empresa
        # Si es superusuario de Django puede auditar sin restricción
        if user.is_superuser:
            return empresa
        if not empresa.activo:
            raise PermissionDenied("La suscripción de tu empresa no está activa o el pago está pendiente.")
        return empresa
    raise PermissionDenied("El usuario no tiene una empresa asignada en el sistema.")


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def usuario_actual(request):
    get_user_empresa(request.user)  # Bloquea si la empresa tiene activo=False
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.all()
    permission_classes = [IsAuthenticated]  # noqa: RUF012
    filter_backends = [filters.SearchFilter] # noqa: RUF012
    search_fields = ['nombre', 'apellido', 'dni'] # noqa: RUF012

    def get_queryset(self):
        empresa = get_user_empresa(self.request.user)
        return Cliente.objects.filter(empresa=empresa, activo=True).order_by('-id')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ClientePerfilSerializer
        return ClienteSerializer

    def perform_create(self, serializer):
        empresa = get_user_empresa(self.request.user)
        
        # Restricción: Plan Independiente permite un máximo de 150 clientes activos
        if empresa.plan_nombre != 'plan_pro':
            clientes_activos = Cliente.objects.filter(empresa=empresa, activo=True).count()
            if clientes_activos >= 150:
                raise serializers.ValidationError(
                    {"error": "Has alcanzado el límite de 150 clientes activos de tu plan Independiente. Pásate al plan Financiera Pro para clientes ilimitados."}
                )

        serializer.save(empresa=empresa)

    def destroy(self, request, *args, **kwargs):
        cliente = self.get_object()
        prestamos_activos = cliente.prestamos.filter(estado__in=['activo', 'mora'])
        if prestamos_activos.exists():
            return Response(
                {"error": "No se puede eliminar el cliente porque tiene préstamos activos o en mora."},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], pagination_class=None)
    def todos(self, request):
        clientes = self.filter_queryset(self.get_queryset())
        serializer = ClienteResumenSerializer(clientes, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def cuotas_cobrables(self, request, pk=None):
        cliente = self.get_object()
        prestamos = cliente.prestamos.filter(estado__in=['activo', 'mora'], activo=True)

        proximas_cuotas = []
        hoy = timezone.localdate()

        for p in prestamos:
            cuotas_pendientes = p.cuotas.filter(esta_pagada=False).order_by('numero_cuota')

            for cuota in cuotas_pendientes:
                monto_total = Decimal(str(getattr(cuota, 'monto_total', Decimal('0.00')) or '0.00'))
                monto_pagado = Decimal(str(getattr(cuota, 'monto_pagado', Decimal('0.00')) or '0.00'))

                if hasattr(cuota, 'saldo_pendiente') and cuota.saldo_pendiente is not None:
                    saldo_capital = Decimal(str(cuota.saldo_pendiente))
                else:
                    saldo_capital = max(Decimal('0.00'), monto_total - monto_pagado)

                mora_acumulada = Decimal('0.00')
                dias_atraso = 0

                if cuota.fecha_vencimiento and cuota.fecha_vencimiento < hoy:
                    dias_atraso = (hoy - cuota.fecha_vencimiento).days

                    if hasattr(cuota, 'calcular_mora'):
                        mora_calc = Decimal(str(cuota.calcular_mora() or '0.00'))
                        mora_pagada = Decimal(str(getattr(cuota, 'mora_pagada', Decimal('0.00')) or '0.00'))
                        mora_acumulada = max(Decimal('0.00'), mora_calc - mora_pagada)
                    else:
                        tasa_diaria = Decimal('0.01')
                        mora_acumulada = monto_total * tasa_diaria * Decimal(str(dias_atraso))

                monto_total_cobrable = saldo_capital + mora_acumulada

                proximas_cuotas.append({
                    "cuota_id": cuota.id,
                    "prestamo_id": p.id,
                    "numero_cuota": cuota.numero_cuota,
                    "monto_total": str(monto_total),
                    "monto_pagado": str(monto_pagado),
                    "saldo_pendiente": str(saldo_capital),
                    "mora": str(mora_acumulada),
                    "monto": str(monto_total_cobrable),
                    "dias_atraso": dias_atraso,
                    "fecha_vencimiento": cuota.fecha_vencimiento,
                    "prestamo_nombre": f"Préstamo #{p.id}"
                })

        return Response(proximas_cuotas)


class GarantiaClienteViewSet(viewsets.ModelViewSet):
    queryset = GarantiaCliente.objects.all()
    permission_classes = [IsAuthenticated] # noqa: RUF012
    serializer_class = GarantiaClienteSerializer
    parser_classes = (parsers.MultiPartParser, parsers.FormParser)

    def get_queryset(self):
        empresa = get_user_empresa(self.request.user)
        queryset = GarantiaCliente.objects.filter(cliente__empresa=empresa)
        cliente_id = self.request.query_params.get('cliente')
        if cliente_id:
            queryset = queryset.filter(cliente_id=cliente_id)
        return queryset


class PrestamoViewSet(viewsets.ModelViewSet):
    queryset = Prestamo.objects.all()
    permission_classes = [IsAuthenticated] # noqa: RUF012
    serializer_class = PrestamoSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter] # noqa: RUF012
    filterset_class = PrestamoFilter
    search_fields = ['cliente__nombre', 'cliente__apellido', 'cliente__dni'] # noqa: RUF012
    ordering_fields = ['fecha_inicio', 'monto_solicitado'] # noqa: RUF012

    def get_queryset(self):
        empresa = get_user_empresa(self.request.user)
        return Prestamo.objects.filter(empresa=empresa, activo=True).order_by('-id')

    def destroy(self, request, *args, **kwargs):
        prestamo = self.get_object()
        with transaction.atomic():
            Caja.objects.filter(prestamo=prestamo).delete()
            return super().destroy(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        empresa = get_user_empresa(request.user)
        data = request.data.copy()

        if not data.get('fecha_inicio'):
            data['fecha_inicio'] = timezone.now()

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        monto_solicitado = Decimal(str(serializer.validated_data['monto_solicitado']))

        with transaction.atomic():
            ingresos = Caja.objects.filter(empresa=empresa, tipo='ingreso').aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
            egresos = Caja.objects.filter(empresa=empresa, tipo='egreso').aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
            saldo_disponible = Decimal(str(ingresos - egresos))

            if saldo_disponible < monto_solicitado:
                return Response(
                    {
                        "error": "Fondos insuficientes en la caja de su empresa para entregar el préstamo.",
                        "saldo_actual": float(saldo_disponible),
                        "monto_requerido": float(monto_solicitado)
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            prestamo = serializer.save(empresa=empresa)
            prestamo.generar_plan_pagos()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['get'], url_path='comprobante-desembolso')
    def generar_comprobante_desembolso(self, request, pk=None):
        try:
            prestamo = self.get_object()
            operador = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
            pdf_buffer = generar_pdf_desembolso_seguro(prestamo, operador_nombre=operador)
            response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="Comprobante_Desembolso_{prestamo.id}.pdf"'
            return response
        except Exception as e:  # noqa: BLE001
            return HttpResponse(f"Error al generar PDF: {e!s}", status=500)

    @action(detail=True, methods=['post'], url_path='registrar-pago')
    def registrar_pago(self, request, pk=None):
        empresa = get_user_empresa(request.user)
        monto_raw = request.data.get('monto')
        metodo_pago_raw = request.data.get('metodo_pago', 'efectivo')
        metodo_pago_detalle = str(request.data.get('metodo_pago_detalle', '')).strip()

        if str(metodo_pago_raw).lower() == 'otro' and metodo_pago_detalle:
            metodo_pago_final = metodo_pago_detalle
        else:
            metodo_pago_final = metodo_pago_raw

        if monto_raw is None or monto_raw == "":
            return Response({"error": "Debe proporcionar el monto del pago."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            monto_limpio = str(monto_raw).replace('$', '').replace(',', '').strip()
            monto_disponible = Decimal(monto_limpio)
            if monto_disponible <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError, TypeError):
            return Response({"error": f"Formato de monto inválido: {monto_raw}"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            prestamo = Prestamo.objects.select_for_update().get(pk=pk, empresa=empresa)
            cuotas_pendientes = list(
                prestamo.cuotas.select_for_update().filter(esta_pagada=False).order_by('numero_cuota')
            )

            if not cuotas_pendientes:
                return Response({"error": "Este préstamo no tiene cuotas pendientes de pago."}, status=status.HTTP_400_BAD_REQUEST)

            monto_inicial = monto_disponible
            desglose = []

            for cuota in cuotas_pendientes:
                if monto_disponible <= 0:
                    break

                mora_cuota = cuota.calcular_mora()
                mora_abonada = Decimal('0.00')
                capital_abonado = Decimal('0.00')

                if mora_cuota > 0:
                    mora_abonada = min(monto_disponible, mora_cuota)
                    cuota.mora_pagada += mora_abonada
                    monto_disponible -= mora_abonada

                if monto_disponible > 0:
                    falta = cuota.saldo_pendiente
                    capital_abonado = min(monto_disponible, falta)
                    cuota.monto_pagado += capital_abonado
                    monto_disponible -= capital_abonado

                    if cuota.saldo_pendiente == Decimal('0.00'):
                        cuota.esta_pagada = True
                        cuota.fecha_pago_real = timezone.localdate()

                cuota.metodo_pago = metodo_pago_final
                cuota.save()

                desglose.append({
                    "cuota_id": cuota.id,
                    "numero_cuota": cuota.numero_cuota,
                    "mora_abonada": float(mora_abonada),
                    "capital_abonado": float(capital_abonado),
                    "cuota_saldada": cuota.esta_pagada,
                    "saldo_pendiente_cuota": float(cuota.saldo_pendiente)
                })

            monto_aplicado = monto_inicial - monto_disponible

            if monto_aplicado > 0:
                cliente = prestamo.cliente
                nombre_cliente = f"{cliente.apellido.upper()} {cliente.nombre.upper()}" if cliente else "CLIENTE"
                concepto = f"COBRO PAGO PREST #{prestamo.id} - CLIENTE: {nombre_cliente}"
                cuota_impactada_id = desglose[0]["cuota_id"] if desglose else None

                Caja.objects.create(
                    empresa=empresa,
                    tipo='ingreso',
                    monto=monto_aplicado,
                    concepto=concepto,
                    metodo_pago=metodo_pago_final,
                    prestamo=prestamo,
                    cuota_id=cuota_impactada_id
                )

            if hasattr(prestamo, 'actualizar_estado_mora'):
                prestamo.actualizar_estado_mora()

            todas_pagadas = not prestamo.cuotas.filter(esta_pagada=False).exists()
            if todas_pagadas:
                prestamo.estado = 'finalizado'
                prestamo.save()

            return Response({
                "success": True,
                "message": "Pago procesado y registrado en caja correctamente.",
                "monto_ingresado": float(monto_inicial),
                "monto_aplicado": float(monto_aplicado),
                "monto_sobrante": float(monto_disponible),
                "prestamo_finalizado": todas_pagadas,
                "desglose": desglose
            }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def sincronizar_mora(self, request):
        empresa = get_user_empresa(request.user)
        prestamos_activos = Prestamo.objects.filter(empresa=empresa, estado__in=['activo', 'mora'])
        actualizados = 0

        for p in prestamos_activos:
            if hasattr(p, 'actualizar_estado_mora') and p.actualizar_estado_mora():
                actualizados += 1

        return Response({
            "message": f"Sincronización completada. {actualizados} préstamos cambiaron de estado."
        })


class CuotaViewSet(viewsets.ModelViewSet):
    queryset = Cuota.objects.all()
    permission_classes = [IsAuthenticated] # noqa: RUF012
    serializer_class = CuotaSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter] # noqa: RUF012
    filterset_class = CuotaFilter
    ordering_fields = ['fecha_vencimiento', 'numero_cuota'] # noqa: RUF012

    def get_queryset(self):
        empresa = get_user_empresa(self.request.user)
        return Cuota.objects.filter(prestamo__empresa=empresa)

    @action(detail=True, methods=['post'])
    def registrar_pago(self, request, pk=None):
        empresa = get_user_empresa(request.user)
        try:
            with transaction.atomic():
                cuota_actual = Cuota.objects.select_for_update().get(pk=pk, prestamo__empresa=empresa)

                if cuota_actual.esta_pagada:
                    return Response(
                        {'error': 'Esta cuota ya fue pagada anteriormente.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                cuotas_anteriores_pendientes = Cuota.objects.filter(
                    prestamo=cuota_actual.prestamo,
                    numero_cuota__lt=cuota_actual.numero_cuota,
                    esta_pagada=False
                ).exists()

                if cuotas_anteriores_pendientes:
                    return Response(
                        {
                            'error': 'No se puede cobrar esta cuota. El cliente debe pagar las cuotas anteriores primero.',
                            'detalle': f'Existen cuotas previas a la #{cuota_actual.numero_cuota} pendientes de pago.'
                        },
                        status=status.HTTP_400_BAD_REQUEST
                    )

                usuario_obj = request.user
                operador = usuario_obj.get_full_name() or usuario_obj.username

                cuota_actual.esta_pagada = True
                cuota_actual.fecha_pago_real = timezone.localdate()
                cuota_actual.save()

                HistorialCuota.objects.create(
                    cuota=cuota_actual,
                    estado_anterior="Pendiente",
                    estado_nuevo="Pagada",
                    usuario=operador,
                    observaciones="Pago procesado con éxito vía API."
                )

                return Response({'status': 'Pago registrado con éxito'}, status=status.HTTP_200_OK)
        except Cuota.DoesNotExist:
            return Response({'error': 'Cuota no encontrada'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:  # noqa: BLE001
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def generar_recibo(self, request, pk=None):
        cuota = self.get_object()

        monto_pagado = getattr(cuota, 'monto_pagado', Decimal('0.00'))
        mora_pagada = getattr(cuota, 'mora_pagada', Decimal('0.00'))

        if monto_pagado <= 0 and mora_pagada <= 0 and not cuota.esta_pagada:
            return Response(
                {'error': 'No se puede generar recibo de una cuota que no registra ningún pago.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        cobrador = request.user.get_full_name() or request.user.username if request.user and request.user.is_authenticated else "Sistema"
        buffer = generar_recibo_pago_pdf(cuota, cobrador_nombre=cobrador)

        filename = f"Recibo_P#{cuota.prestamo.id}_C#{cuota.numero_cuota}.pdf"
        return HttpResponse(
            buffer,
            content_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )


class CajaViewSet(viewsets.ModelViewSet):
    queryset = Caja.objects.all()
    serializer_class = CajaSerializer
    permission_classes = [IsAuthenticated] # noqa: RUF012
    filter_backends = [DjangoFilterBackend, filters.SearchFilter] # noqa: RUF012
    filterset_class = CajaFilter
    search_fields = ['concepto'] # noqa: RUF012

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Caja.objects.none()

        empresa = get_user_empresa(user)
        if user.is_staff or (hasattr(user, 'permisos_custom') and user.permisos_custom.puede_ver_caja):
            return Caja.objects.filter(empresa=empresa).order_by('-fecha', '-id')

        return Caja.objects.none()

    def perform_create(self, serializer):
        empresa = get_user_empresa(self.request.user)
        serializer.save(empresa=empresa)


class CambiarPasswordView(APIView):
    permission_classes = [IsAuthenticated] # noqa: RUF012

    def post(self, request, *args, **kwargs):
        serializer = CambiarPasswordSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = request.user
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            return Response({"message": "Contraseña actualizada correctamente."}, status=status.HTTP_200_OK)

        error_msg = next(iter(serializer.errors.values()))[0]
        return Response({"error": error_msg}, status=status.HTTP_400_BAD_REQUEST)


class CajaDiariaViewSet(viewsets.ModelViewSet):
    queryset = CajaDiaria.objects.all()
    serializer_class = CajaDiariaSerializer
    permission_classes = [IsAuthenticated] # noqa: RUF012

    def get_queryset(self):
        empresa = get_user_empresa(self.request.user)
        return CajaDiaria.objects.filter(empresa=empresa)

    @action(detail=False, methods=['get'])
    def estado_actual(self, request):
        empresa = get_user_empresa(request.user)
        caja_abierta = CajaDiaria.objects.filter(empresa=empresa, estado='ABIERTA').first()

        if caja_abierta:
            ingresos = sum(mov.monto for mov in caja_abierta.movimientos.filter(tipo='ingreso'))
            egresos = sum(mov.monto for mov in caja_abierta.movimientos.filter(tipo='egreso'))
            estimado = caja_abierta.saldo_apertura + ingresos - egresos

            op = caja_abierta.operador_apertura
            nombre_operador = f"{op.first_name} {op.last_name}".strip() or op.username

            return Response({
                'caja_abierta': True,
                'id': caja_abierta.id,
                'fecha': caja_abierta.fecha,
                'saldo_apertura': caja_abierta.saldo_apertura,
                'ingresos_sistema': ingresos,
                'egresos_sistema': egresos,
                'saldo_estimado': estimado,
                'operador_apertura': nombre_operador
            })

        ultima_caja = CajaDiaria.objects.filter(empresa=empresa, estado='CERRADA').order_by('-id').first()
        saldo_sugerido = ultima_caja.saldo_real_fisico if ultima_caja else Decimal('0.00')

        return Response({
            'caja_abierta': False,
            'saldo_sugerido': saldo_sugerido
        })

    @action(detail=False, methods=['post'])
    def abrir_caja(self, request):
        empresa = get_user_empresa(request.user)
        saldo_inicial = request.data.get('saldo_apertura', 0.00)

        try:
            with transaction.atomic():
                nueva_caja = CajaDiaria(
                    empresa=empresa,
                    operador_apertura=request.user,
                    saldo_apertura=saldo_inicial,
                    estado='ABIERTA'
                )
                nueva_caja.full_clean()
                nueva_caja.save()

                serializer = self.get_serializer(nueva_caja)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({'error': str(e.message_dict if hasattr(e, 'message_dict') else e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def cerrar_caja(self, request, pk=None):
        empresa = get_user_empresa(request.user)
        saldo_fisico = request.data.get('saldo_real_fisico')
        observaciones = request.data.get('observaciones', '')

        if saldo_fisico is None or saldo_fisico == '':
            return Response({'error': 'Debes proveer el saldo real físico contado de la caja.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                caja = CajaDiaria.objects.select_for_update().get(pk=pk, empresa=empresa)

                if caja.estado == 'CERRADA':
                    return Response({'error': 'Esta caja ya se encuentra cerrada.'}, status=status.HTTP_400_BAD_REQUEST)

                ingresos = sum(mov.monto for mov in caja.movimientos.filter(tipo='ingreso'))
                egresos = sum(mov.monto for mov in caja.movimientos.filter(tipo='egreso'))

                caja.ingresos_sistema = ingresos
                caja.egresos_sistema = egresos
                caja.saldo_estimado = caja.saldo_apertura + ingresos - egresos

                caja.saldo_real_fisico = Decimal(str(saldo_fisico))
                caja.diferencia = caja.saldo_real_fisico - caja.saldo_estimado

                caja.observaciones = observaciones
                caja.operador_cierre = request.user
                caja.fecha_cierre = timezone.now()
                caja.estado = 'CERRADA'
                caja.save()

                serializer = self.get_serializer(caja)
                return Response(serializer.data, status=status.HTTP_200_OK)
        except CajaDiaria.DoesNotExist:
            return Response({'error': 'Caja diaria no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:  # noqa: BLE001
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CrearOperadorView(APIView):
    permission_classes = [IsAdminUser]  # noqa: RUF012

    def get(self, request):
        empresa = get_user_empresa(request.user)
        perfiles = PerfilUsuario.objects.filter(empresa=empresa).exclude(user=request.user).select_related('user')
        usuarios = [p.user for p in perfiles]
        serializer = UserSerializer(usuarios, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        empresa = get_user_empresa(request.user)

        # Restricción: Solo el plan Financiera Pro admite la creación de operadores
        if empresa.plan_nombre != 'plan_pro':
            return Response(
                {
                    'error': 'Tu plan Independiente solo admite 1 usuario administrador. Actualiza a Financiera Pro para sumar operadores a tu equipo.'
                },
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = CrearOperadorSerializer(data=request.data, context={'empresa': empresa})
        if serializer.is_valid():
            user = serializer.save()
            return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RegistroEmpresaView(APIView):
    permission_classes = [AllowAny]  # noqa: RUF012

    def post(self, request):
        data = request.data
        nombre_empresa = data.get('nombre_empresa', '').strip()
        cuit_rut = data.get('cuit_rut', '').strip()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        email = data.get('email', '').strip()
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        plan_id = data.get('plan_id', 'plan_basico')

        if not nombre_empresa or not username or not password:
            return Response(
                {'error': 'Nombre de empresa, usuario y contraseña son requeridos.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if User.objects.filter(username=username).exists():
            return Response(
                {'error': 'El nombre de usuario ya está en uso. Por favor elige otro.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Precios según plan
        precio = 29000.0 if plan_id == 'plan_pro' else 15000.0
        titulo_plan = "PrestaYa - Plan Financiera Pro" if plan_id == 'plan_pro' else "PrestaYa - Plan Independiente"

        try:
            with transaction.atomic():
                # 1. Crear Empresa (inactiva hasta que pague)
                empresa = Empresa.objects.create(
                    nombre=nombre_empresa,
                    cuit_rut=cuit_rut,
                    activo=False,
                    plan_nombre=plan_id
                )

                # 2. Crear Usuario Administrador
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    is_staff=True
                )

                # 3. Vincular Perfil
                PerfilUsuario.objects.create(
                    user=user,
                    empresa=empresa,
                    rol='admin'
                )

                # 4. Generar Preferencia en Mercado Pago
                access_token = os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')
                init_point = None

                if access_token:
                    sdk = mercadopago.SDK(access_token)
                    preference_data = {
                        "items": [
                            {
                                "title": titulo_plan,
                                "quantity": 1,
                                "unit_price": float(precio),
                                "currency_id": "ARS"
                            }
                        ],
                        "payer": {
                            "email": email or "cliente@prestaya-app.com",
                            "name": first_name or username,
                            "surname": last_name or ""
                        },
                        "notification_url": "https://version-obsessed-pajamas.ngrok-free.dev/api/webhooks/mercadopago/",
                        "back_urls": {
                            "success": "https://version-obsessed-pajamas.ngrok-free.dev/login?pago=exitoso",
                            "failure": "https://version-obsessed-pajamas.ngrok-free.dev/registro?pago=fallido",
                            "pending": "https://version-obsessed-pajamas.ngrok-free.dev/registro?pago=pendiente"
                        },
                        "auto_return": "approved",
                        "external_reference": f"empresa_{empresa.id}",
                    }
                    pref_res = sdk.preference().create(preference_data)
                    print("--> STATUS PREF MP:", pref_res.get("status"))

                    if pref_res.get("status") in [200, 201]:
                        res_data = pref_res.get("response", {})
                        init_point = res_data.get("sandbox_init_point") or res_data.get("init_point")
                        empresa.mp_preference_id = res_data.get("id")
                        empresa.save(update_fields=['mp_preference_id'])
                    else:
                        print("--> ERROR RESPUESTA MP:", pref_res)

                return Response({
                    'mensaje': 'Registro iniciado. Redirigiendo al pago...',
                    'init_point': init_point,
                    'empresa_id': empresa.id,
                    'username': user.username
                }, status=status.HTTP_201_CREATED)

        except Exception as e:  # noqa: BLE001
            return Response(
                {'error': f'Error en el registro: {str(e)}'},  # noqa: RUF010
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class MercadoPagoWebhookView(APIView):
    permission_classes = [AllowAny]  # noqa: RUF012
    authentication_classes = [] # noqa: RUF012

    def post(self, request):
        topic = request.GET.get('topic') or request.data.get('type')
        payment_id = request.GET.get('id') or request.data.get('data', {}).get('id')

        print(f"--> Webhook recibido - Topic: {topic}, ID: {payment_id}")

        if topic == 'payment' and payment_id:
            access_token = os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')
            sdk = mercadopago.SDK(access_token)
            payment_info = sdk.payment().get(payment_id)

            if payment_info.get("status") == 200:
                p_data = payment_info["response"]
                print(f"--> Estado del pago MP: {p_data.get('status')}")
                if p_data.get("status") == "approved":
                    external_ref = str(p_data.get("external_reference", ""))
                    
                    if external_ref.startswith("empresa_"):
                        empresa_id = external_ref.split("_")[1]
                    else:
                        empresa_id = external_ref

                    try:
                        emp = Empresa.objects.get(id=empresa_id)
                        emp.activo = True
                        emp.mp_payment_id = str(payment_id)
                        emp.fecha_ultimo_pago = timezone.now()
                        emp.save()
                        print(f"--> [ÉXITO] Empresa {emp.nombre} (ID: {emp.id}) activada por Webhook.")
                    except Empresa.DoesNotExist:
                        print(f"--> [ERROR] Empresa con ID {empresa_id} no encontrada.")

        return Response(status=status.HTTP_200_OK)
    

class MetricasAvanzadasProView(APIView):
    permission_classes = [IsAuthenticated]  # noqa: RUF012

    def get(self, request):
        empresa = get_user_empresa(request.user)

        # Solo Plan Pro
        if empresa.plan_nombre != 'plan_pro':
            return Response(
                {"error": "El módulo de Métricas y Analítica Avanzada es exclusivo del Plan Financiera Pro."},
                status=status.HTTP_403_FORBIDDEN
            )

        hoy = timezone.localdate()

        # Score y Distribución de Riesgo de Cuotas Pendientes
        cuotas_pendientes = Cuota.objects.filter(prestamo__empresa=empresa, esta_pagada=False)
        total_pendientes = cuotas_pendientes.count()

        al_dia_count = cuotas_pendientes.filter(fecha_vencimiento__gte=hoy).count()
        mora_leve_count = cuotas_pendientes.filter(
            fecha_vencimiento__lt=hoy,
            fecha_vencimiento__gte=hoy - timedelta(days=15)
        ).count()
        mora_critica_count = cuotas_pendientes.filter(
            fecha_vencimiento__lt=hoy - timedelta(days=15)
        ).count()

        distribucion_riesgo = {
            "al_dia": al_dia_count,
            "mora_temprana": mora_leve_count,
            "mora_critica": mora_critica_count,
            "porcentaje_sano": round((al_dia_count / total_pendientes * 100), 1) if total_pendientes > 0 else 100
        }

        # Flujo Proyectado de Cobranzas a 4 Semanas (Cashflow futuro)
        proyeccion_semanas = []
        for i in range(4):
            inicio_sem = hoy + timedelta(days=i * 7)
            fin_sem = inicio_sem + timedelta(days=6)
            total_esperado = cuotas_pendientes.filter(
                fecha_vencimiento__range=[inicio_sem, fin_sem]
            ).aggregate(total=Sum('monto_total'))['total'] or Decimal('0.00')

            proyeccion_semanas.append({
                "semana": f"Semana {i+1} ({inicio_sem.strftime('%d/%m')})",
                "monto_esperado": float(total_esperado)
            })

        # Desempeño del Equipo de Operadores (Cobros registrados en caja)
        movimientos_operadores = (
            Caja.objects.filter(empresa=empresa, tipo='ingreso')
            .exclude(concepto__isnull=True)
            .values('prestamo__operador__username')
            .annotate(
                total_cobrado=Sum('monto'),
                operaciones=Count('id')
            )
            .order_by('-total_cobrado')[:5]
        )

        return Response({
            "distribucion_riesgo": distribucion_riesgo,
            "proyeccion_semanas": proyeccion_semanas,
            "kpis_operadores": list(movimientos_operadores),
        }, status=status.HTTP_200_OK)