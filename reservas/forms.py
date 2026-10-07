from django import forms
from django.utils import timezone

from .models import CAPACIDADES, Reserva, Veiculo
from .services import reservas_conflitantes, veiculos_disponiveis


class VeiculoForm(forms.ModelForm):
    class Meta:
        model = Veiculo
        fields = ["codigo", "categoria", "ativo"]


class ReservaForm(forms.ModelForm):
    veiculo = forms.ModelChoiceField(
        queryset=Veiculo.objects.filter(ativo=True), required=False
    )
    categoria_pretendida = forms.ChoiceField(
        choices=Veiculo.Categoria.choices, required=False
    )

    class Meta:
        model = Reserva
        fields = [
            "solicitante", "setor", "atividade", "origem", "destino",
            "data", "hora_saida", "hora_retorno", "passageiros",
            "veiculo", "categoria_pretendida", "observacoes",
        ]

    def clean(self):
        dados = super().clean()
        data = dados.get("data")
        saida = dados.get("hora_saida")
        retorno = dados.get("hora_retorno")
        pax = dados.get("passageiros")
        veiculo = dados.get("veiculo")
        categoria = dados.get("categoria_pretendida")

        # Data e horários
        if data and data < timezone.localdate():
            self.add_error("data", "A data não pode estar no passado.")
        if saida and retorno and retorno <= saida:
            self.add_error("hora_retorno", "O horário de retorno deve ser posterior ao de saída.")

        # Capacidade
        if pax is not None:
            if pax < 1:
                self.add_error("passageiros", "Informe ao menos 1 passageiro.")
            elif pax > 18:
                self.add_error("passageiros", "Solicitação rejeitada: máximo de 18 passageiros.")
            elif veiculo and pax > veiculo.capacidade:
                self.add_error(
                    "passageiros",
                    f"O veículo {veiculo.codigo} comporta {veiculo.capacidade} passageiros "
                    f"e foram solicitados {pax}.",
                )
            elif categoria and pax > CAPACIDADES[categoria]:
                self.add_error(
                    "passageiros",
                    f"A categoria {categoria} comporta {CAPACIDADES[categoria]} passageiros. "
                    "Escolha a categoria COLETIVO.",
                )

        if not veiculo and not categoria:
            self.add_error(None, "Informe o veículo ou a categoria pretendida.")

        if self.errors:          # só consulta o banco se o resto estiver válido
            return dados

        # Conflito de horários (consulta ao banco)
        ignorar = self.instance.pk
        if veiculo:
            if reservas_conflitantes(veiculo, data, saida, retorno, ignorar).exists():
                self.add_error(
                    "veiculo",
                    f"Conflito: {veiculo.codigo} já está reservado nesse intervalo.",
                )
        else:
            escolhido = veiculos_disponiveis(data, saida, retorno, pax, categoria, ignorar).first()
            if escolhido is None:
                self.add_error(
                    "categoria_pretendida",
                    "Nenhum veículo dessa categoria está disponível no período.",
                )
            else:
                dados["veiculo"] = escolhido   # atribuição automática
        return dados