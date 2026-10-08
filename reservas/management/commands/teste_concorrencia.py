import json
import threading
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from django.utils import timezone

from reservas.models import Reserva, Veiculo


class Command(BaseCommand):
    help = "Dispara reservas simultâneas no mesmo veículo/horário e confere que só uma é aceita"

    def add_arguments(self, parser):
        parser.add_argument("--threads", type=int, default=10)

    def handle(self, *args, **opts):
        n = opts["threads"]
        User = get_user_model()
        user, _ = User.objects.get_or_create(username="teste_concorrencia", defaults={"is_staff": True})
        veiculo, _ = Veiculo.objects.get_or_create(codigo="ZZ-TESTE", defaults={"categoria": "LEVE"})
        dia = (timezone.localdate() + timedelta(days=60)).isoformat()
        corpo = json.dumps({
            "solicitante": "Teste", "setor": "QA", "atividade": "Concorrência",
            "origem": "A", "destino": "B", "data": dia,
            "hora_saida": "09:00", "hora_retorno": "11:00",
            "passageiros": 2, "veiculo": veiculo.id,
        })
        resultados = []
        largada = threading.Barrier(n)

        def disparar():
            try:
                c = Client(HTTP_HOST="127.0.0.1")
                c.force_login(user)
                largada.wait(timeout=60)  # todas saem juntas
                resp = c.post("/api/reservas/", corpo, content_type="application/json")
                resultados.append(resp.status_code)
            except Exception as exc:
                resultados.append(f"erro: {exc!r}")
            finally:
                connection.close()

        try:
            threads = [threading.Thread(target=disparar) for _ in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            aceitas = resultados.count(201)
            conflitos = resultados.count(409)
            outros = [r for r in resultados if r not in (201, 409)]
            self.stdout.write(f"{n} requisições simultâneas -> aceitas (201): {aceitas} | "
                              f"conflito (409): {conflitos} | outras: {outros}")
            if aceitas == 1 and not outros:
                self.stdout.write(self.style.SUCCESS("OK: apenas 1 reserva foi aceita."))
            else:
                self.stdout.write(self.style.ERROR("FALHOU: reservas duplicadas ou erro."))
        finally:
            Reserva.objects.filter(veiculo=veiculo).delete()
            veiculo.delete()
            user.delete()