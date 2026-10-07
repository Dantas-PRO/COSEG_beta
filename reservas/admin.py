from django.contrib import admin
from .models import Reserva, Veiculo

@admin.register(Veiculo)
class VeiculoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "categoria", "capacidade", "ativo")

@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ("veiculo", "data", "hora_saida", "hora_retorno", "atividade", "status")
    list_filter = ("data", "veiculo", "status")