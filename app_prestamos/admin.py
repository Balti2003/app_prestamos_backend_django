from django.contrib import admin
from .models import Prestamo, Cuota, Caja, Cliente


# Register your models here.
admin.site.register(Prestamo)
admin.site.register(Cuota)
admin.site.register(Caja)
admin.site.register(Cliente)