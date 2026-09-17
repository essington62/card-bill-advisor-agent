"""Testes do motor de cálculo determinístico (motor_decisao/calculo.py)."""

from __future__ import annotations

import pytest

from motor_decisao.calculo import (
    TAXAS_MENSAIS,
    calcular_features_derivadas,
    calcular_juros_compostos,
    comparar_estrategias,
    estrategia_a_parcial_rotativo,
    estrategia_b_parcelamento_total,
    estrategia_c_parcial_mais_credito,
    estrategia_d_adiar_total,
    taxa_credito_disponivel,
)


# ---------------------------------------------------------------------------
# Regra dos 2 meses: bloqueia A e D quando rolou_mes_anterior=True
# ---------------------------------------------------------------------------


def test_estrategia_a_bloqueada_se_rolou_mes_anterior():
    assert estrategia_a_parcial_rotativo(1000.0, 500.0, rolou_mes_anterior=True) is None


def test_estrategia_d_bloqueada_se_rolou_mes_anterior():
    assert estrategia_d_adiar_total(rolou_mes_anterior=True) is None


def test_estrategia_a_e_d_viaveis_se_nao_rolou():
    assert estrategia_a_parcial_rotativo(1000.0, 500.0, rolou_mes_anterior=False) is not None
    assert estrategia_d_adiar_total(rolou_mes_anterior=False) is not None


def test_comparar_estrategias_exclui_a_e_d_quando_rolou():
    resultado = comparar_estrategias(
        fatura_total=3000.0,
        valor_disponivel_agora=500.0,
        vinculo="informal",
        rolou_mes_anterior=True,
        dias_apos_vencimento=5,
    )
    nomes = {item["estrategia"] for item in resultado["estrategias_viaveis"]}
    assert "estrategia_a_parcial_rotativo" not in nomes
    assert "estrategia_d_adiar_total" not in nomes
    assert "estrategia_c_parcial_mais_credito" in nomes


# ---------------------------------------------------------------------------
# Teto regulatório de 2x (CMN 5.112, art. 2º-C)
# ---------------------------------------------------------------------------


def test_teto_2x_aplicado_em_parcelamento_longo_sem_entrada():
    # 24x a 9.65% a.m. sem entrada gera ~2.6x o principal — deve ser
    # cortado para exatamente 2x a fatura original.
    resultado = estrategia_b_parcelamento_total(
        fatura_total=8000.0, n_parcelas=24, dias_apos_vencimento=-1
    )
    assert resultado["teto_aplicado"] is True
    assert resultado["valor_total_final"] == pytest.approx(16000.0)


def test_teto_2x_nao_aplicado_em_parcelamento_curto():
    # 12x a 9.65% a.m. gera ~1.73x o principal — não deve acionar o teto.
    resultado = estrategia_b_parcelamento_total(
        fatura_total=8000.0, n_parcelas=12, dias_apos_vencimento=-1
    )
    assert resultado["teto_aplicado"] is False
    assert resultado["valor_total_final"] < 16000.0


def test_janela_apos_15_dias_marca_inviavel_sem_calcular_teto():
    resultado = estrategia_b_parcelamento_total(
        fatura_total=1000.0, n_parcelas=6, dias_apos_vencimento=16
    )
    assert resultado["viavel"] is False
    assert resultado["valor_total_final"] is None


# ---------------------------------------------------------------------------
# Elegibilidade por vínculo empregatício
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vinculo,produto_esperado",
    [
        ("servidor_publico", "consignado_publico"),
        ("aposentado_inss", "consignado_inss"),
        # MP 1.292/2025: CLT privado acessa consignado_privado direto (via
        # eSocial), sem depender de convênio — não há mais variante "sem
        # convênio" para clt_privado.
        ("clt_privado", "consignado_privado"),
        ("profissional_liberal", "credito_pessoal"),
        ("informal", "credito_pessoal"),
    ],
)
def test_taxa_credito_disponivel_por_vinculo(vinculo, produto_esperado):
    produto, taxa = taxa_credito_disponivel(vinculo)
    assert produto == produto_esperado
    assert taxa == TAXAS_MENSAIS[produto_esperado]


def test_vinculo_desconhecido_levanta_value_error():
    with pytest.raises(ValueError):
        taxa_credito_disponivel("estagiario")


# ---------------------------------------------------------------------------
# comparar_estrategias: ordenação por custo final
# ---------------------------------------------------------------------------


def test_comparar_estrategias_ordena_por_custo_crescente():
    resultado = comparar_estrategias(
        fatura_total=4000.0,
        valor_disponivel_agora=1000.0,
        vinculo="clt_privado",
        rolou_mes_anterior=False,
        dias_apos_vencimento=-3,
    )
    viaveis = resultado["estrategias_viaveis"]
    assert len(viaveis) >= 3

    custos = [
        item["resultado"]["valor_total_final"]
        for item in viaveis
        if item["resultado"]["valor_total_final"] is not None
    ]
    assert custos == sorted(custos)
    assert resultado["recomendada"] == viaveis[0]
    assert resultado["economia_vs_pior_opcao"] == pytest.approx(custos[-1] - custos[0])


# ---------------------------------------------------------------------------
# Validações de entrada (ValueError)
# ---------------------------------------------------------------------------


def test_calcular_juros_compostos_rejeita_valor_negativo():
    with pytest.raises(ValueError):
        calcular_juros_compostos(-100.0, 5.0, 12)


def test_estrategia_b_rejeita_n_parcelas_fora_do_intervalo():
    with pytest.raises(ValueError):
        estrategia_b_parcelamento_total(1000.0, n_parcelas=3, dias_apos_vencimento=0)
    with pytest.raises(ValueError):
        estrategia_b_parcelamento_total(1000.0, n_parcelas=25, dias_apos_vencimento=0)


def test_estrategia_b_rejeita_entrada_fora_de_5_20_pct():
    with pytest.raises(ValueError):
        estrategia_b_parcelamento_total(
            1000.0, n_parcelas=6, dias_apos_vencimento=-1, valor_entrada=1.0
        )
    with pytest.raises(ValueError):
        estrategia_b_parcelamento_total(
            1000.0, n_parcelas=6, dias_apos_vencimento=-1, valor_entrada=300.0
        )


def test_estrategia_c_rejeita_valor_pago_maior_que_fatura():
    with pytest.raises(ValueError):
        estrategia_c_parcial_mais_credito(1000.0, 1500.0, "informal", 12)


# ---------------------------------------------------------------------------
# calcular_features_derivadas
# ---------------------------------------------------------------------------

_CLIENTE_BASE = {
    "bill_amount": 2000.0,
    "monthly_income": 4000.0,
    "credit_limit": 5000.0,
    "available_limit": 3000.0,
    "minimum_payment": 300.0,
    "previous_bill_amount": 1800.0,
    "previous_payment_amount": 1800.0,
    "previous_revolving": False,
    "consecutive_partial_payments": 0,
}


def test_calcular_features_derivadas_caso_normal():
    features = calcular_features_derivadas(_CLIENTE_BASE)
    assert features["bill_to_income"] == pytest.approx(0.5)
    assert features["credit_utilization"] == pytest.approx(0.4)
    assert features["payment_ratio"] == pytest.approx(1.0)
    assert features["minimum_payment_ratio"] == pytest.approx(0.15)
    assert features["revolving_flag"] is False
    assert features["partial_payment_flag"] is False
    assert features["available_cash_ratio"] == pytest.approx(0.6)


def test_calcular_features_derivadas_payment_ratio_none_sem_fatura_anterior():
    cliente = {**_CLIENTE_BASE, "previous_bill_amount": 0.0, "previous_payment_amount": 0.0}
    features = calcular_features_derivadas(cliente)
    assert features["payment_ratio"] is None


def test_calcular_features_derivadas_flags_de_risco():
    cliente = {
        **_CLIENTE_BASE,
        "previous_revolving": True,
        "consecutive_partial_payments": 2,
        "previous_payment_amount": 600.0,
    }
    features = calcular_features_derivadas(cliente)
    assert features["revolving_flag"] is True
    assert features["partial_payment_flag"] is True
    assert features["payment_ratio"] == pytest.approx(600.0 / 1800.0)


def test_calcular_features_derivadas_rejeita_renda_nao_positiva():
    with pytest.raises(ValueError):
        calcular_features_derivadas({**_CLIENTE_BASE, "monthly_income": 0.0})


def test_calcular_features_derivadas_rejeita_limite_nao_positivo():
    with pytest.raises(ValueError):
        calcular_features_derivadas({**_CLIENTE_BASE, "credit_limit": 0.0})


# ---------------------------------------------------------------------------
# Vínculo é escopo exclusivo da Estratégia C
# ---------------------------------------------------------------------------


def test_estrategia_a_nao_recebe_vinculo():
    import inspect

    assert "vinculo" not in inspect.signature(estrategia_a_parcial_rotativo).parameters


def test_estrategia_b_nao_recebe_vinculo():
    import inspect

    assert "vinculo" not in inspect.signature(estrategia_b_parcelamento_total).parameters


# ---------------------------------------------------------------------------
# Capacidade de pagamento mensal (affordability)
# ---------------------------------------------------------------------------


def test_sem_capacidade_informada_mantem_comportamento_por_custo():
    resultado = comparar_estrategias(
        fatura_total=6000.0,
        valor_disponivel_agora=0.0,
        vinculo="informal",
        rolou_mes_anterior=True,
        dias_apos_vencimento=10,
        n_parcelas_parcelamento=24,
        n_parcelas_credito=6,
    )
    assert resultado["alerta_capacidade"] is False
    assert resultado["estrategias_cabem_no_orcamento"] == resultado["estrategias_viaveis"]
    assert resultado["estrategias_fora_do_orcamento"] == []
    # Sem restrição de orçamento, vence a mais barata no total.
    assert resultado["recomendada"]["estrategia"] == "estrategia_c_parcial_mais_credito"


def test_recomendacao_muda_quando_mais_barata_nao_cabe_no_orcamento():
    # C (crédito pessoal 6x) é mais barata no total (~R$ 6.867) mas tem
    # parcela mensal de ~R$ 1.144 — acima da capacidade de R$ 700.
    # B (24x) é mais cara no total (~R$ 12.000) mas cabe (~R$ 487/mês).
    resultado = comparar_estrategias(
        fatura_total=6000.0,
        valor_disponivel_agora=0.0,
        vinculo="informal",
        rolou_mes_anterior=True,
        dias_apos_vencimento=10,
        n_parcelas_parcelamento=24,
        n_parcelas_credito=6,
        capacidade_mensal_maxima=700.0,
    )
    nomes_fora = {item["estrategia"] for item in resultado["estrategias_fora_do_orcamento"]}
    nomes_cabem = {item["estrategia"] for item in resultado["estrategias_cabem_no_orcamento"]}

    assert "estrategia_c_parcial_mais_credito" in nomes_fora
    assert "estrategia_b_parcelamento_total" in nomes_cabem
    assert resultado["recomendada"]["estrategia"] == "estrategia_b_parcelamento_total"
    assert resultado["alerta_capacidade"] is False


def test_alerta_capacidade_quando_nenhuma_estrategia_cabe():
    # Capacidade de R$ 100/mês: nem C (~1.144) nem B (~487) cabem.
    # Recomendação deve cair na de MENOR parcela mensal (B), não na de
    # menor custo total (C), com alerta_capacidade=True.
    resultado = comparar_estrategias(
        fatura_total=6000.0,
        valor_disponivel_agora=0.0,
        vinculo="informal",
        rolou_mes_anterior=True,
        dias_apos_vencimento=10,
        n_parcelas_parcelamento=24,
        n_parcelas_credito=6,
        capacidade_mensal_maxima=100.0,
    )
    assert resultado["estrategias_cabem_no_orcamento"] == []
    assert resultado["alerta_capacidade"] is True
    assert resultado["recomendada"]["estrategia"] == "estrategia_b_parcelamento_total"
    assert "mensagem_alerta_capacidade" in resultado


def test_estrategias_sem_parcela_mensal_sempre_cabem_no_orcamento():
    # A (rotativo) não tem parcela mensal — deve sempre cair em
    # "estrategias_cabem_no_orcamento", mesmo com capacidade baixíssima.
    resultado = comparar_estrategias(
        fatura_total=3000.0,
        valor_disponivel_agora=1000.0,
        vinculo="informal",
        rolou_mes_anterior=False,
        dias_apos_vencimento=-5,
        capacidade_mensal_maxima=1.0,
    )
    nomes_cabem = {item["estrategia"] for item in resultado["estrategias_cabem_no_orcamento"]}
    assert "estrategia_a_parcial_rotativo" in nomes_cabem
