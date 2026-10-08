from django import forms
from django.core.exceptions import ValidationError

from .models import Reserva, Veiculo
from .services import CAPACIDADES, MAX_PASSAGEIROS, travar_veiculos, validar_periodo, veiculos_disponiveis


class VeiculoForm(forms.ModelForm):
    class Meta:
        model = Veiculo
        fields = ["codigo", "categoria", "ativo"]


class ReservaForm(forms.ModelForm):
    # queryset com TODOS os veículos: quem diz se o inativo pode ou não é o model
    veiculo = forms.ModelChoiceField(queryset=Veiculo.objects.all(), required=False)
    categoria_pretendida = forms.ChoiceField(
        choices=Veiculo.Categoria.choices, required=False
    )

    class Meta:
        model = Reserva
        fields = [
            "solicitante", "setor", "atividade", "origem", "destino",
            "data", "hora_saida", "data_retorno", "hora_retorno", "passageiros",
            "veiculo", "categoria_pretendida", "observacoes",
        ]

    def clean(self):
        dados = super().clean()
        veiculo = dados.get("veiculo")
        categoria = dados.get("categoria_pretendida")

        if veiculo is None and not categoria and "veiculo" not in self.errors:
            self.add_error(None, ValidationError(
                "Informe o veículo ou a categoria pretendida.", code="veiculo_obrigatorio"))
            return dados

        # Atribuição automática: só categoria informada
        if veiculo is None and categoria and "veiculo" not in self.errors:
            data = dados.get("data")
            saida = dados.get("hora_saida")
            retorno = dados.get("hora_retorno")
            data_retorno = dados.get("data_retorno")
            pax = dados.get("passageiros")
            pronto = (
                None not in (data, saida, retorno, pax)
                and 1 <= pax <= CAPACIDADES[categoria]
                and not validar_periodo(data, saida, retorno, data_retorno, checar_passado=False)
            )
            if pronto:
                travar_veiculos()  # trava a frota até o fim da transação
                escolhido = veiculos_disponiveis(
                    data, saida, retorno, pax, categoria, data_retorno, self.instance.pk
                ).first()
                if escolhido is None:
                    self.add_error("categoria_pretendida", ValidationError(
                        "Nenhum veículo dessa categoria está disponível no período.",
                        code="sem_veiculo_disponivel"))
                else:
                    dados["veiculo"] = escolhido
        return dados