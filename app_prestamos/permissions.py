from rest_framework.permissions import BasePermission


class IsAdminUserOnly(BasePermission):
    """
    Permiso personalizado: Permite el acceso ÚNICAMENTE a Administradores (is_staff = True).
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsAdminOrReadOnly(BasePermission):
    """
    Permiso personalizado:
    - Usuarios autenticados (Operadores/Invitados) pueden LEER (GET, HEAD, OPTIONS).
    - Solo Administradores pueden CREAR, EDITAR o ELIMINAR (POST, PUT, DELETE).
    """
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        
        # Si la petición es solo de lectura (GET), lo dejamos pasar
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return True
            
        # Para escribir/modificar/borrar exige ser admin
        return request.user.is_staff