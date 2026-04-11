from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.response import Response
from .models import Cliente, Prestamo, Cuota
from .serializers import ClienteSerializer, PrestamoSerializer, CuotaSerializer


class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer

class PrestamoViewSet(viewsets.ModelViewSet):
    queryset = Prestamo.objects.all()
    serializer_class = PrestamoSerializer

    def create(self, request, *args, **kwargs):
        # Sobrescribimos el método create para disparar la lógica de cuotas
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Guardamos el préstamo
        prestamo = serializer.save()
        
        # Disparamos la función que definimos en el modelo
        prestamo.generar_plan_pagos()
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

class CuotaViewSet(viewsets.ModelViewSet):
    queryset = Cuota.objects.all()
    serializer_class = CuotaSerializer