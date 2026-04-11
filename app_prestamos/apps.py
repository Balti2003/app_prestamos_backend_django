from django.apps import AppConfig

class AppPrestamosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'app_prestamos'

    def ready(self):
        import app_prestamos.signals  # Esto activa las señales automáticamente