from datetime import date, time

from django.core.management.base import BaseCommand

from reservas.models import Reserva, Veiculo


class Command(BaseCommand):
    help = "Carrega a frota e as reservas iniciais do COSEG"

    def handle(self, *args, **opts):
        for i in range(1, 9):
            Veiculo.objects.get_or_create(codigo=f"VL-{i:02d}", defaults={"categoria": "LEVE"})
        for i in range(1, 3):
            Veiculo.objects.get_or_create(codigo=f"VC-{i:02d}", defaults={"categoria": "COLETIVO"})

        iniciais = [
            ("VL-01", date(2026, 8, 18), time(8), time(10), "Reunião administrativa", 3),
            ("VL-03", date(2026, 8, 18), time(13), time(15), "Inspeção técnica", 2),
            ("VC-01", date(2026, 8, 19), time(9), time(12), "Treinamento", 15),
            ("VL-05", date(2026, 8, 20), time(14), time(17), "Visita externa", 4),
        ]
        for cod, d, s, r, ativ, pax in iniciais:
            veiculo = Veiculo.objects.get(codigo=cod)
            if Reserva.objects.filter(veiculo=veiculo, data=d, hora_saida=s).exists():
                continue
            # Carga histórica do enunciado (datas já passadas): bypass explícito da validação.
            Reserva(
                veiculo=veiculo, data=d, hora_saida=s, hora_retorno=r,
                solicitante="COSEG (carga inicial)", setor="COSEG", atividade=ativ,
                origem="Porto do Itaqui", destino="A definir", passageiros=pax,
            ).save(validar=False)
        self.stdout.write(self.style.SUCCESS("Dados iniciais carregados."))