import json
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import Reserva, Veiculo

JSON = "application/json"


def codigos(resposta):
    """Todos os 'code' de erro de uma resposta de validação."""
    return {e["code"] for lista in resposta.json()["erros"].values() for e in lista}


@override_settings(COSEG_PERMITIR_DATA_PASSADA=False)  # testes não dependem do .env
class BaseAPI(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.coseg = User.objects.create_user("coseg", password="Senha#Teste123", is_staff=True)
        self.colab = User.objects.create_user("colab", password="Senha#Teste123")
        self.vl = Veiculo.objects.create(codigo="VL-01", categoria="LEVE")
        self.vl2 = Veiculo.objects.create(codigo="VL-02", categoria="LEVE")
        self.vc = Veiculo.objects.create(codigo="VC-01", categoria="COLETIVO")
        self.dia = timezone.localdate() + timedelta(days=30)
        self.c = Client()
        self.c.force_login(self.coseg)
        self.c_colab = Client()
        self.c_colab.force_login(self.colab)

    def dados(self, **extra):
        base = {
            "solicitante": "Ana", "setor": "TI", "atividade": "Reunião",
            "origem": "Itaqui", "destino": "Centro", "data": self.dia.isoformat(),
            "hora_saida": "08:00", "hora_retorno": "10:00",
            "passageiros": 3, "veiculo": self.vl.id,
        }
        base.update(extra)
        return base

    def post(self, dados, client=None):
        return (client or self.c).post("/api/reservas/", json.dumps(dados), content_type=JSON)

    def put(self, url, dados, client=None):
        return (client or self.c).put(url, json.dumps(dados), content_type=JSON)


class ReservaAPITests(BaseAPI):
    # ---------- básico ----------
    def test_persistencia_e_crud(self):
        r = self.post(self.dados())
        self.assertEqual(r.status_code, 201)
        pk = r.json()["reserva"]["id"]
        self.assertTrue(Reserva.objects.filter(pk=pk).exists())
        r = self.put(f"/api/reservas/{pk}/", self.dados(passageiros=2))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Reserva.objects.get(pk=pk).passageiros, 2)
        self.assertEqual(self.c.delete(f"/api/reservas/{pk}/").status_code, 200)
        self.assertFalse(Reserva.objects.filter(pk=pk).exists())

    def test_campos_obrigatorios(self):
        r = self.post({})
        self.assertEqual(r.status_code, 400)
        self.assertTrue({"solicitante", "setor", "data", "hora_saida"} <= set(r.json()["erros"]))

    def test_erros_aparecem_juntos(self):
        ontem = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.post(self.dados(data=ontem, hora_saida="10:00", hora_retorno="09:00", passageiros=50))
        self.assertEqual(r.status_code, 400)
        self.assertTrue({"data", "hora_retorno", "passageiros"} <= set(r.json()["erros"]))

    # ---------- capacidade ----------
    def test_capacidade_leve(self):
        r = self.post(self.dados(passageiros=7))
        self.assertEqual(r.status_code, 400)
        self.assertIn("capacidade_excedida", codigos(r))

    def test_acima_de_18(self):
        r = self.post(self.dados(passageiros=19, veiculo=self.vc.id))
        self.assertEqual(r.status_code, 400)
        self.assertIn("acima_do_limite", codigos(r))

    def test_categoria_incompativel(self):
        r = self.post(self.dados(categoria_pretendida="COLETIVO"))  # veículo é leve
        self.assertEqual(r.status_code, 400)
        self.assertIn("categoria_incompativel", codigos(r))

    def test_aviso_coletivo_poucos_passageiros(self):
        r = self.post(self.dados(veiculo=self.vc.id, passageiros=1))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(r.json()["avisos"]), 1)

    # ---------- data e horários ----------
    def test_data_passada(self):
        ontem = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.post(self.dados(data=ontem))
        self.assertEqual(r.status_code, 400)
        self.assertIn("data_passada", codigos(r))

    def test_horario_passado_hoje(self):
        hoje = timezone.localdate()
        fixo = datetime.combine(hoje, time(12, 0))  # "agora" = 12:00
        with mock.patch("reservas.services.timezone.localtime", return_value=fixo):
            r = self.post(self.dados(data=hoje.isoformat(), hora_saida="08:00", hora_retorno="09:00"))
            self.assertEqual(r.status_code, 400)
            self.assertIn("horario_passado", codigos(r))
            r = self.post(self.dados(data=hoje.isoformat(), hora_saida="13:00", hora_retorno="14:00"))
            self.assertEqual(r.status_code, 201)

    def test_retorno_antes_da_saida(self):
        r = self.post(self.dados(hora_saida="10:00", hora_retorno="09:00"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("retorno_invalido", codigos(r))

    def test_viagem_passa_da_meia_noite(self):
        d1 = self.dia + timedelta(days=1)
        r = self.post(self.dados(hora_saida="22:00", hora_retorno="02:00", data_retorno=d1.isoformat()))
        self.assertEqual(r.status_code, 201)
        # sobrepõe a madrugada seguinte
        r = self.post(self.dados(data=d1.isoformat(), hora_saida="01:00", hora_retorno="03:00"))
        self.assertEqual(r.status_code, 409)
        # encostando no fim é permitido
        r = self.post(self.dados(data=d1.isoformat(), hora_saida="02:00", hora_retorno="03:00"))
        self.assertEqual(r.status_code, 201)
        # retorno muito depois da saída é recusado
        longe = (self.dia + timedelta(days=5)).isoformat()
        r = self.post(self.dados(hora_saida="08:00", hora_retorno="09:00", data_retorno=longe))
        self.assertEqual(r.status_code, 400)

    # ---------- conflito ----------
    def test_conflito_sobreposicao(self):
        self.assertEqual(self.post(self.dados()).status_code, 201)
        r = self.post(self.dados(hora_saida="09:00", hora_retorno="11:00"))
        self.assertEqual(r.status_code, 409)
        self.assertIn("conflito_horario", codigos(r))
        self.assertIn("reserva #", r.json()["erros"]["veiculo"][0]["message"])

    def test_sem_conflito(self):
        self.post(self.dados())
        r = self.post(self.dados(hora_saida="10:30", hora_retorno="12:00"))
        self.assertEqual(r.status_code, 201)

    def test_reservas_encostadas(self):
        self.post(self.dados())  # 08-10
        r = self.post(self.dados(hora_saida="10:00", hora_retorno="11:00"))
        self.assertEqual(r.status_code, 201)

    def _reserva_no_passado(self):
        self.passado = timezone.localdate() - timedelta(days=10)
        # carga histórica: bypass explícito da validação (como o seed_dados)
        Reserva(
            solicitante="COSEG", setor="COSEG", atividade="Reunião administrativa",
            origem="Itaqui", destino="X", data=self.passado,
            hora_saida=time(8), hora_retorno=time(10), passageiros=3, veiculo=self.vl,
        ).save(validar=False)

    def test_conflito_em_data_passada_mostra_os_dois_erros(self):
        self._reserva_no_passado()
        r = self.post(self.dados(data=self.passado.isoformat(), hora_saida="09:00", hora_retorno="11:00"))
        self.assertEqual(r.status_code, 409)
        self.assertTrue({"data_passada", "conflito_horario"} <= codigos(r))

    @override_settings(COSEG_PERMITIR_DATA_PASSADA=True)
    def test_conflito_do_enunciado_no_modo_demonstracao(self):
        self._reserva_no_passado()
        r = self.post(self.dados(data=self.passado.isoformat(), hora_saida="09:00", hora_retorno="11:00"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(codigos(r), {"conflito_horario"})
        r = self.post(self.dados(data=self.passado.isoformat(), hora_saida="10:30", hora_retorno="12:00"))
        self.assertEqual(r.status_code, 201)

    def test_atribuicao_automatica_por_categoria(self):
        dados = self.dados(passageiros=10, categoria_pretendida="COLETIVO")
        dados.pop("veiculo")
        r = self.post(dados)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["reserva"]["veiculo"]["codigo"], "VC-01")

    # ---------- edição, cancelamento, veículos ----------
    def test_editar_sem_informar_veiculo_mantem_veiculo(self):
        pk = self.post(self.dados()).json()["reserva"]["id"]
        r = self.put(f"/api/reservas/{pk}/", {"observacoes": "novo"})
        self.assertEqual(r.status_code, 200)
        reserva = Reserva.objects.get(pk=pk)
        self.assertEqual(reserva.veiculo_id, self.vl.id)
        self.assertEqual(reserva.observacoes, "novo")

    def test_cancelamento(self):
        pk = self.post(self.dados()).json()["reserva"]["id"]
        self.assertEqual(self.c.post(f"/api/reservas/{pk}/cancelar/").status_code, 200)
        self.assertEqual(self.c.post(f"/api/reservas/{pk}/cancelar/").status_code, 409)
        self.assertEqual(self.put(f"/api/reservas/{pk}/", {"observacoes": "x"}).status_code, 409)
        # o horário foi liberado
        self.assertEqual(self.post(self.dados()).status_code, 201)

    def test_veiculo_inativo(self):
        pk = self.post(self.dados()).json()["reserva"]["id"]
        Veiculo.objects.filter(pk=self.vl.pk).update(ativo=False)
        url = f"/api/reservas/{pk}/"
        # editar dados que não mexem na agenda continua permitido
        self.assertEqual(self.put(url, {"observacoes": "ajuste"}).status_code, 200)
        # mexer na agenda ou criar reserva nele, não
        r = self.put(url, {"hora_saida": "07:00"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("veiculo_inativo", codigos(r))
        r = self.post(self.dados(hora_saida="13:00", hora_retorno="15:00"))
        self.assertIn("veiculo_inativo", codigos(r))
        # realocar para outro veículo é permitido
        self.assertEqual(self.put(url, {"veiculo": self.vl2.id}).status_code, 200)

    def test_mudar_categoria_com_reservas_futuras(self):
        self.assertEqual(self.post(self.dados(veiculo=self.vc.id, passageiros=15)).status_code, 201)
        r = self.put(f"/api/veiculos/{self.vc.id}/", {"categoria": "LEVE"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("reservas_incompativeis", codigos(r))
        self.vc.refresh_from_db()
        self.assertEqual(self.vc.categoria, "COLETIVO")

    # ---------- API ----------
    def test_api_responde_json_nos_erros(self):
        casos = [("get", "/api/reservas/9999/", 404),
                 ("get", "/api/nao-existe/", 404),
                 ("delete", "/api/disponibilidade/", 405)]
        for metodo, url, esperado in casos:
            r = getattr(self.c, metodo)(url)
            self.assertEqual(r.status_code, esperado, url)
            self.assertIn("application/json", r["Content-Type"])
            self.assertFalse(r.json()["sucesso"])

    def test_entradas_invalidas_nao_derrubam_o_servidor(self):
        self.assertEqual(self.c.get("/api/reservas/?data=abc").status_code, 400)
        for corpo in ["[]", "{", "null", "[" * 50000]:
            r = self.c.post("/api/reservas/", corpo, content_type=JSON)
            self.assertEqual(r.status_code, 400, corpo[:10])
        r = self.post({"passageiros": "abc", "veiculo": {"a": 1}, "data": [1]})
        self.assertEqual(r.status_code, 400)

    def test_tipos_errados_no_cadastro(self):
        ruins = [[], [1], {}, {"a": 1}, 123, 1.5, True]
        for campo in ("data", "hora_saida", "data_retorno", "hora_retorno"):
            for ruim in ruins:
                r = self.post(self.dados(**{campo: ruim}))
                self.assertEqual(r.status_code, 400, (campo, ruim))
                self.assertIn("tipo_invalido", codigos(r))
        for campo in ("passageiros", "veiculo"):
            for ruim in ([], {"a": 1}, True, 1.5):
                r = self.post(self.dados(**{campo: ruim}))
                self.assertEqual(r.status_code, 400, (campo, ruim))
        self.assertEqual(Reserva.objects.count(), 0)

    def test_tipos_errados_na_edicao(self):
        pk = self.post(self.dados()).json()["reserva"]["id"]
        for campo in ("data", "hora_saida", "data_retorno", "hora_retorno"):
            for ruim in ([], [1], {"a": 1}, 123, True):
                for metodo in (self.put, self.c.patch):
                    if metodo == self.put:
                        r = self.put(f"/api/reservas/{pk}/", {campo: ruim})
                    else:
                        r = self.c.patch(f"/api/reservas/{pk}/", json.dumps({campo: ruim}),
                                         content_type=JSON)
                    self.assertEqual(r.status_code, 400, (campo, ruim))
                    self.assertIn("tipo_invalido", codigos(r))

    def test_tipos_errados_em_veiculo(self):
        for corpo in [{"codigo": [1], "categoria": "LEVE"},
                      {"codigo": "ZZ-1", "categoria": {"a": 1}},
                      {"codigo": "ZZ-1", "categoria": "LEVE", "ativo": [1]},
                      {"codigo": 123, "categoria": "LEVE"},
                      {"codigo": "ZZ-1", "categoria": "XYZ"}]:
            r = self.c.post("/api/veiculos/", json.dumps(corpo), content_type=JSON)
            self.assertEqual(r.status_code, 400, corpo)
        self.assertFalse(Veiculo.objects.filter(codigo="ZZ-1").exists())

    def test_disponibilidade_valida(self):
        r = self.c.get("/api/disponibilidade/", {
            "data": self.dia.isoformat(), "saida": "08:00", "retorno": "10:00", "passageiros": 3})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["disponiveis"]), 3)

    def test_disponibilidade_invalida(self):
        ontem = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.c.get("/api/disponibilidade/", {
            "data": ontem, "saida": "08:00", "retorno": "10:00", "passageiros": 3})
        self.assertEqual(r.status_code, 400)
        self.assertIn("data_passada", codigos(r))
        r = self.c.get("/api/disponibilidade/", {
            "data": self.dia.isoformat(), "saida": "08:00", "retorno": "10:00",
            "passageiros": 3, "categoria": "XYZ"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("categoria", r.json()["erros"])
        r = self.c.get("/api/disponibilidade/", {
            "data": self.dia.isoformat(), "saida": "08:00", "retorno": "10:00", "passageiros": 0})
        self.assertEqual(r.status_code, 400)


class ORMTests(BaseAPI):
    """As regras valem também para quem grava direto pelo ORM."""

    def campos(self, **extra):
        base = dict(solicitante="X", setor="Y", atividade="Z", origem="A", destino="B",
                    data=self.dia, hora_saida=time(8), hora_retorno=time(10),
                    passageiros=3, veiculo=self.vl)
        base.update(extra)
        return base

    def test_orm_aplica_as_regras(self):
        with self.assertRaises(ValidationError) as ctx:
            Reserva.objects.create(**self.campos(
                data=date(2020, 1, 1), hora_saida=time(10), hora_retorno=time(9), passageiros=50))
        self.assertTrue({"data", "hora_retorno", "passageiros"} <= set(ctx.exception.message_dict))
        self.assertEqual(Reserva.objects.count(), 0)

    def test_orm_aceita_dados_validos_e_barra_conflito(self):
        r = Reserva.objects.create(**self.campos(
            data=self.dia.isoformat(), hora_saida="08:00", hora_retorno="10:00"))
        self.assertEqual(r.data, self.dia)  # texto convertido para data
        with self.assertRaises(ValidationError) as ctx:
            Reserva.objects.create(**self.campos(hora_saida=time(9), hora_retorno=time(11)))
        self.assertIn("conflito_horario", {e.code for e in ctx.exception.error_dict["veiculo"]})
        self.assertEqual(Reserva.objects.count(), 1)

    def test_orm_tipos_errados_viram_validation_error(self):
        casos = [("data", "abc"), ("data", [1]), ("data", 123), ("hora_saida", "xx"),
                 ("hora_saida", [1]), ("hora_retorno", {"a": 1}), ("data_retorno", 123),
                 ("passageiros", "abc"), ("passageiros", [1]), ("categoria_pretendida", [1])]
        for campo, ruim in casos:
            with self.assertRaises(ValidationError, msg=(campo, ruim)):
                Reserva.objects.create(**self.campos(**{campo: ruim}))
        self.assertEqual(Reserva.objects.count(), 0)

    def test_orm_veiculo_categoria_inexistente(self):
        with self.assertRaises(ValidationError):
            Veiculo.objects.create(codigo="XX-01", categoria="XYZ")
        with self.assertRaises(ValidationError):
            Veiculo.objects.create(codigo="XX-02", categoria=[1])
        with self.assertRaises(ValidationError):  # código duplicado: erro de validação, não IntegrityError
            Veiculo.objects.create(codigo="VL-01", categoria="LEVE")
        self.assertFalse(Veiculo.objects.filter(codigo__startswith="XX").exists())

    def test_bypass_explicito_para_carga_inicial(self):
        Reserva(**self.campos(data=date(2020, 1, 1))).save(validar=False)
        self.assertEqual(Reserva.objects.count(), 1)


class AdminTests(BaseAPI):
    def test_admin_aplica_as_mesmas_regras(self):
        admin = get_user_model().objects.create_superuser("admin", "a@a.com", "Senha#Teste123")
        c = Client()
        c.force_login(admin)
        ontem = (timezone.localdate() - timedelta(days=1)).isoformat()
        form = {
            "solicitante": "Ana", "setor": "TI", "atividade": "X", "origem": "A", "destino": "B",
            "data": ontem, "hora_saida": "10:00", "data_retorno": "", "hora_retorno": "09:00",
            "passageiros": 50, "veiculo": self.vl.id, "categoria_pretendida": "",
            "observacoes": "", "status": "ATIVA", "_save": "Salvar",
        }
        r = c.post("/admin/reservas/reserva/add/", form)
        self.assertEqual(r.status_code, 200)  # voltou ao formulário com erros
        erros = r.context["adminform"].form.errors.get_json_data()
        self.assertTrue({"data", "hora_retorno", "passageiros"} <= set(erros))
        self.assertEqual(Reserva.objects.count(), 0)

        valido = dict(form, data=self.dia.isoformat(), hora_retorno="11:00", passageiros=3)
        r = c.post("/admin/reservas/reserva/add/", valido)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Reserva.objects.get().criado_por, admin)


class SegurancaAPITests(BaseAPI):
    def login(self, client, user, senha):
        return client.post("/api/login/", json.dumps({"username": user, "password": senha}),
                           content_type=JSON)

    def test_sem_login_retorna_401(self):
        anonimo = Client()
        for url in ["/api/veiculos/", "/api/reservas/", "/api/disponibilidade/"]:
            self.assertEqual(anonimo.get(url).status_code, 401)

    def test_colaborador_nao_gerencia_frota(self):
        corpo = {"codigo": "VL-99", "categoria": "LEVE", "ativo": True}
        r = self.c_colab.post("/api/veiculos/", json.dumps(corpo), content_type=JSON)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c_colab.delete(f"/api/veiculos/{self.vl.id}/").status_code, 403)

    def test_colaborador_so_ve_as_proprias_reservas(self):
        self.post(self.dados())
        self.assertEqual(self.c_colab.get("/api/reservas/").json()["reservas"], [])
        r = self.post(self.dados(hora_saida="13:00", hora_retorno="15:00"), self.c_colab)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(self.c_colab.get("/api/reservas/").json()["reservas"]), 1)
        self.assertEqual(len(self.c.get("/api/reservas/").json()["reservas"]), 2)

    def test_colaborador_nao_acessa_reserva_alheia(self):
        pk = self.post(self.dados()).json()["reserva"]["id"]
        self.assertEqual(self.c_colab.get(f"/api/reservas/{pk}/").status_code, 403)
        self.assertEqual(self.c_colab.post(f"/api/reservas/{pk}/cancelar/").status_code, 403)

    def test_colaborador_nao_exclui_reserva(self):
        pk = self.post(self.dados(), self.c_colab).json()["reserva"]["id"]
        self.assertEqual(self.c_colab.delete(f"/api/reservas/{pk}/").status_code, 403)
        self.assertEqual(self.c_colab.post(f"/api/reservas/{pk}/cancelar/").status_code, 200)

    def test_login_e_logout(self):
        c = Client()
        self.assertEqual(self.login(c, "coseg", "errada").status_code, 401)
        self.assertEqual(self.login(c, "coseg", "Senha#Teste123").status_code, 200)
        self.assertEqual(c.get("/api/eu/").status_code, 200)
        c.post("/api/logout/")
        self.assertEqual(c.get("/api/eu/").status_code, 401)

    def test_bloqueio_apos_muitas_tentativas(self):
        c = Client()
        for _ in range(5):
            self.login(c, "coseg", "errada")
        self.assertEqual(self.login(c, "coseg", "Senha#Teste123").status_code, 429)

    def test_csrf_exigido_nas_escritas(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.coseg)
        r = self.post(self.dados(), c)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()["sucesso"])