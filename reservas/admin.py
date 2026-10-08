from django.contrib import admin

from .models import Reserva, Veiculo


@admin.register(Veiculo)
class VeiculoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "categoria", "capacidade", "ativo")
    list_filter = ("categoria", "ativo")


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ("veiculo", "data", "hora_saida", "data_retorno", "hora_retorno",
                    "atividade", "status", "criado_por")
    list_filter = ("data", "veiculo", "status")
    readonly_fields = ("criado_por",)

    def save_model(self, request, obj, form, change):
        if not change and obj.criado_por_id is None:
            obj.criado_por = request.user
        super().save_model(request, obj, form, change)