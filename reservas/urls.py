from django.urls import path
from . import views

urlpatterns = [
    path("veiculos/", views.veiculos_lista),
    path("veiculos/<int:pk>/", views.veiculo_detalhe),
    path("reservas/", views.reservas_lista),
    path("reservas/<int:pk>/", views.reserva_detalhe),
    path("reservas/<int:pk>/cancelar/", views.reserva_cancelar),
    path("disponibilidade/", views.disponibilidade),
]