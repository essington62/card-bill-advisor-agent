"""Motor de cálculo determinístico — decisão de pagamento de fatura (Itaú).

Núcleo de lógica pura (sem IO, sem rede, sem LLM/ADK) que um agente
conversacional chama como tool para recomendar a melhor forma de lidar com
uma fatura de cartão que o cliente não consegue pagar integralmente. Todas
as funções são puras: recebem dados, retornam dicts, nunca imprimem nada.
"""

from __future__ import annotations

from typing import Literal

# ---------------------------------------------------------------------------
# Constantes de negócio
# ---------------------------------------------------------------------------

TAXAS_MENSAIS: dict[str, float] = {
    # Fonte: Banco Central, "histórico de taxa de juros diário" — Itaú
    # Unibanco S.A., taxas ao mês, prefixadas (planilha oficial BC).
    "rotativo": 14.70,
    "parcelamento_fatura": 9.65,
    "credito_pessoal": 4.00,
    "consignado_privado": 3.56,
    "consignado_publico": 1.73,
    "consignado_inss": 1.82,
}

Vinculo = Literal[
    "servidor_publico",
    "aposentado_inss",
    "clt_privado",
    "profissional_liberal",
    "informal",
]

_VINCULOS_VALIDOS = frozenset(
    {
        "servidor_publico",
        "aposentado_inss",
        "clt_privado",
        "profissional_liberal",
        "informal",
    }
)

# CMN 5.112, art. 2º-C: em renegociação/parcelamento de fatura, juros e
# encargos não podem fazer o valor final ultrapassar 2x a dívida original.
TETO_MULTIPLICADOR = 2.0

# Janela "Entrada + parcelas fixas" (Itaú): entrada mínima 5%, máxima 20%
# do saldo a financiar.
ENTRADA_MIN_PCT = 0.05
ENTRADA_MAX_PCT = 0.20


# ---------------------------------------------------------------------------
# Elegibilidade de crédito
# ---------------------------------------------------------------------------


def taxa_credito_disponivel(vinculo: str) -> tuple[str, float]:
    """Retorna (produto, taxa_mensal_pct) com a melhor taxa de crédito
    pessoal/consignado disponível para o vínculo empregatício informado.

    Consignado exige desconto em folha garantido — só existe para quem tem
    esse vínculo (servidor público, aposentado/pensionista INSS, ou CLT do
    setor privado). Sem essa garantia, a única opção é crédito pessoal, de
    taxa mais alta.

    MP nº 1.292/2025 ("Crédito do Trabalhador"): CLT do setor privado passou
    a acessar consignado privado diretamente, via integração eSocial, sem
    depender de convênio prévio entre empresa e banco — por isso não existe
    mais parâmetro `tem_convenio` aqui (nem em toda a cadeia que chama esta
    função): todo `clt_privado` é elegível.
    """
    if vinculo == "servidor_publico":
        return "consignado_publico", TAXAS_MENSAIS["consignado_publico"]
    if vinculo == "aposentado_inss":
        return "consignado_inss", TAXAS_MENSAIS["consignado_inss"]
    if vinculo == "clt_privado":
        return "consignado_privado", TAXAS_MENSAIS["consignado_privado"]
    if vinculo in ("profissional_liberal", "informal"):
        return "credito_pessoal", TAXAS_MENSAIS["credito_pessoal"]

    raise ValueError(
        f"Vínculo empregatício desconhecido: {vinculo!r}. "
        f"Valores aceitos: {sorted(_VINCULOS_VALIDOS)}"
    )


# ---------------------------------------------------------------------------
# a) Juros compostos (Price) — função auxiliar
# ---------------------------------------------------------------------------


def calcular_juros_compostos(valor: float, taxa_mensal_pct: float, n_parcelas: int) -> float:
    """Valor total pago (soma das parcelas) ao financiar `valor` em
    `n_parcelas` fixas pelo sistema Price, à taxa `taxa_mensal_pct` a.m.

    Price é o padrão do Itaú em parcelamento de fatura e crédito
    pessoal/consignado: parcela de valor fixo, juros compostos incidindo
    sobre o saldo devedor decrescente.
    """
    if valor < 0:
        raise ValueError("valor não pode ser negativo")
    if taxa_mensal_pct < 0:
        raise ValueError("taxa_mensal_pct não pode ser negativa")
    if n_parcelas < 1:
        raise ValueError("n_parcelas deve ser >= 1")

    if valor == 0:
        return 0.0

    i = taxa_mensal_pct / 100
    if i == 0:
        return valor

    parcela = valor * i / (1 - (1 + i) ** -n_parcelas)
    return parcela * n_parcelas


# ---------------------------------------------------------------------------
# b) Estratégias
# ---------------------------------------------------------------------------


def estrategia_a_parcial_rotativo(
    fatura_total: float, valor_pago_agora: float, rolou_mes_anterior: bool
) -> dict | None:
    """Estratégia A: paga parte da fatura agora, resto vai para o rotativo
    por 1 ciclo (1 mês).

    Regra dos "2 meses seguidos" (Itaú, itau.com.br/.../pagamento-minimo):
    quem já rolou fatura (pagou entre o mínimo e o total) no ciclo anterior
    é automaticamente movido para entrada obrigatória (5%-20%) +
    parcelamento no ciclo seguinte — o rotativo por 2 ciclos seguidos não é
    oferecido. Por isso a estratégia é inviável (retorna None) se
    `rolou_mes_anterior=True`.

    Universal: o rotativo do cartão não depende do vínculo empregatício do
    cliente (diferente da Estratégia C, que usa crédito pessoal/consignado
    elegível por vínculo) — não aceitar/consultar `vinculo` aqui de propósito,
    para não criar acoplamento acidental com `taxa_credito_disponivel`.
    """
    if fatura_total <= 0:
        raise ValueError("fatura_total deve ser positiva")
    if valor_pago_agora < 0 or valor_pago_agora > fatura_total:
        raise ValueError("valor_pago_agora deve estar entre 0 e fatura_total")

    if rolou_mes_anterior:
        return None

    valor_remanescente = fatura_total - valor_pago_agora
    taxa_aplicada = TAXAS_MENSAIS["rotativo"]
    custo_juros = valor_remanescente * taxa_aplicada / 100
    valor_total_final = valor_pago_agora + valor_remanescente + custo_juros

    return {
        "valor_pago_agora": valor_pago_agora,
        "valor_remanescente": valor_remanescente,
        "taxa_aplicada": taxa_aplicada,
        "custo_juros": custo_juros,
        "valor_total_final": valor_total_final,
        "viavel": True,
    }


def _validar_entrada(valor_entrada: float, saldo: float) -> None:
    """Valida que a entrada está entre 5% e 20% do saldo (regra Itaú para
    a modalidade "Entrada + parcelas fixas")."""
    minimo = saldo * ENTRADA_MIN_PCT
    maximo = saldo * ENTRADA_MAX_PCT
    if not (minimo <= valor_entrada <= maximo):
        raise ValueError(
            f"valor_entrada deve estar entre {minimo:.2f} (5%) e "
            f"{maximo:.2f} (20%) do saldo — recebido {valor_entrada:.2f}"
        )


def estrategia_b_parcelamento_total(
    fatura_total: float,
    n_parcelas: int,
    dias_apos_vencimento: int,
    valor_entrada: float | None = None,
) -> dict:
    """Estratégia B: parcela a fatura inteira.

    Janela de contratação (Itaú): até o vencimento (`dias_apos_vencimento
    <= 0`), "Parcela Fixa" (sem entrada) e "Entrada + parcelas fixas"
    (com entrada) estão ambas disponíveis; entre 1 e 15 dias após o
    vencimento, só "Entrada + parcelas fixas" (entrada passa a ser
    obrigatória); após 15 dias, nenhuma modalidade — cai em módulo de
    renegociação, fora de escopo aqui.

    Teto regulatório (CMN 5.112, art. 2º-C): o valor final nunca pode
    ultrapassar 2x o valor original da fatura — aplicado como cap no
    resultado, sinalizado por `teto_aplicado`.

    Universal: parcelamento de fatura é uma modalidade do cartão, disponível
    a qualquer cliente independente do vínculo empregatício (diferente da
    Estratégia C, elegível por vínculo) — não aceitar/consultar `vinculo`
    aqui de propósito, para não criar acoplamento acidental.
    """
    if fatura_total <= 0:
        raise ValueError("fatura_total deve ser positiva")
    if not (4 <= n_parcelas <= 24):
        raise ValueError("n_parcelas deve estar entre 4 e 24")

    if dias_apos_vencimento > 15:
        return {
            "viavel": False,
            "motivo": (
                "Mais de 15 dias após o vencimento: fora da janela de "
                "contratação de parcelamento do Itaú — requer módulo de "
                "renegociação (não implementado aqui)."
            ),
            "valor_entrada": None,
            "valor_parcelado": None,
            "valor_parcela": None,
            "valor_parcela_mensal": None,
            "custo_juros_total": None,
            "valor_total_final": None,
            "teto_aplicado": False,
        }

    exige_entrada = dias_apos_vencimento > 0

    if valor_entrada is None:
        valor_entrada = fatura_total * ENTRADA_MIN_PCT if exige_entrada else 0.0
    else:
        if valor_entrada < 0:
            raise ValueError("valor_entrada não pode ser negativo")
        if exige_entrada or valor_entrada > 0:
            _validar_entrada(valor_entrada, fatura_total)

    valor_parcelado = fatura_total - valor_entrada
    total_com_juros = calcular_juros_compostos(
        valor_parcelado, TAXAS_MENSAIS["parcelamento_fatura"], n_parcelas
    )
    custo_juros_total = total_com_juros - valor_parcelado
    valor_total_final = valor_entrada + total_com_juros

    teto_maximo = fatura_total * TETO_MULTIPLICADOR
    teto_aplicado = valor_total_final > teto_maximo
    if teto_aplicado:
        valor_total_final = teto_maximo
        total_com_juros = valor_total_final - valor_entrada
        custo_juros_total = total_com_juros - valor_parcelado

    valor_parcela = total_com_juros / n_parcelas

    return {
        "viavel": True,
        "motivo": None,
        "valor_entrada": valor_entrada,
        "valor_parcelado": valor_parcelado,
        "valor_parcela": valor_parcela,
        # Alias explícito usado por `comparar_estrategias` para checar
        # capacidade de pagamento mensal (mesmo valor de `valor_parcela`).
        "valor_parcela_mensal": valor_parcela,
        "custo_juros_total": custo_juros_total,
        "valor_total_final": valor_total_final,
        "teto_aplicado": teto_aplicado,
    }


def estrategia_c_parcial_mais_credito(
    fatura_total: float,
    valor_pago_agora: float,
    vinculo: str,
    n_parcelas_credito: int,
) -> dict:
    """Estratégia C: paga parte da fatura agora e financia o restante em
    crédito pessoal ou consignado, na melhor taxa elegível para o vínculo
    empregatício do cliente (`taxa_credito_disponivel`)."""
    if fatura_total <= 0:
        raise ValueError("fatura_total deve ser positiva")
    if valor_pago_agora < 0 or valor_pago_agora > fatura_total:
        raise ValueError("valor_pago_agora deve estar entre 0 e fatura_total")
    if n_parcelas_credito < 1:
        raise ValueError("n_parcelas_credito deve ser >= 1")

    produto_usado, taxa_aplicada = taxa_credito_disponivel(vinculo)

    valor_financiado = fatura_total - valor_pago_agora
    total_com_juros = calcular_juros_compostos(valor_financiado, taxa_aplicada, n_parcelas_credito)
    custo_juros = total_com_juros - valor_financiado
    valor_total_final = valor_pago_agora + total_com_juros
    valor_parcela_mensal = total_com_juros / n_parcelas_credito

    return {
        "valor_pago_agora": valor_pago_agora,
        "valor_financiado": valor_financiado,
        "produto_usado": produto_usado,
        "taxa_aplicada": taxa_aplicada,
        "custo_juros": custo_juros,
        "valor_parcela_mensal": valor_parcela_mensal,
        "valor_total_final": valor_total_final,
        "viavel": True,
    }


def estrategia_d_adiar_total(rolou_mes_anterior: bool) -> dict | None:
    """Estratégia D: não pagar nada agora, adiar a decisão para o próximo
    ciclo. Mesma regra dos 2 meses da estratégia A: se o cliente já rolou
    no ciclo anterior, adiar de novo não é uma opção oferecida pelo banco
    (entrada obrigatória é imposta). Sem custo calculável agora — os
    encargos de atraso só são conhecidos no próximo ciclo.
    """
    if rolou_mes_anterior:
        return None

    return {
        "viavel": True,
        "valor_total_final": None,
        "aviso": (
            "Opção mais arriscada: gera atraso, risco de negativação e "
            "encargos ainda desconhecidos no próximo ciclo — não elimina "
            "a dívida, apenas adia a decisão."
        ),
    }


# ---------------------------------------------------------------------------
# Features derivadas do perfil bruto do cliente
# ---------------------------------------------------------------------------


def calcular_features_derivadas(cliente: dict) -> dict:
    """Deriva indicadores a partir do perfil bruto do cliente (schema
    client_id/month_ref/... — ver consultor_fatura/agent.py e
    consultor_fatura/roteiro_testes_completo.md para o schema completo).

    Função pura e reaproveitável: não decide nada sozinha (não é chamada
    por `comparar_estrategias` nem pelas estratégias A-D), só transforma
    campos brutos em indicadores prontos para uso por quem precisar (ex.:
    scoring de risco, dashboards, heurísticas futuras do agente).

    Espera as chaves: bill_amount, monthly_income, credit_limit,
    available_limit, minimum_payment, previous_bill_amount,
    previous_payment_amount, previous_revolving, consecutive_partial_payments.

    Retorna: bill_to_income, credit_utilization, payment_ratio (None se
    não houve fatura no ciclo anterior), minimum_payment_ratio,
    revolving_flag, partial_payment_flag, available_cash_ratio.
    """
    if cliente["monthly_income"] <= 0:
        raise ValueError("monthly_income deve ser positivo")
    if cliente["credit_limit"] <= 0:
        raise ValueError("credit_limit deve ser positivo")

    bill_amount = cliente["bill_amount"]
    previous_bill_amount = cliente["previous_bill_amount"]

    payment_ratio = (
        cliente["previous_payment_amount"] / previous_bill_amount
        if previous_bill_amount > 0
        else None
    )

    return {
        "bill_to_income": bill_amount / cliente["monthly_income"],
        "credit_utilization": bill_amount / cliente["credit_limit"],
        "payment_ratio": payment_ratio,
        "minimum_payment_ratio": cliente["minimum_payment"] / bill_amount,
        "revolving_flag": bool(cliente["previous_revolving"]),
        "partial_payment_flag": cliente["consecutive_partial_payments"] > 0,
        "available_cash_ratio": cliente["available_limit"] / cliente["credit_limit"],
    }


# ---------------------------------------------------------------------------
# f) Orquestrador
# ---------------------------------------------------------------------------


def comparar_estrategias(
    fatura_total: float,
    valor_disponivel_agora: float,
    vinculo: str,
    rolou_mes_anterior: bool,
    dias_apos_vencimento: int,
    n_parcelas_parcelamento: int = 12,
    n_parcelas_credito: int = 12,
    capacidade_mensal_maxima: float | None = None,
) -> dict:
    """Chama as estratégias A-D, filtra as viáveis (`viavel=True`) e ordena
    por `valor_total_final` crescente. Estratégias sem custo calculável (D)
    vão para o fim da lista, mas continuam elegíveis.

    Etapa 2 (affordability) — só roda se `capacidade_mensal_maxima` for
    informado: entre as viáveis, separa as que cabem no orçamento mensal do
    cliente (`valor_parcela_mensal <= capacidade_mensal_maxima`, ou sem
    parcela mensal, como A e D) das que não cabem. A recomendação passa a
    ser a mais barata dentre as que cabem; se nenhuma couber, recomenda a de
    MENOR parcela mensal (não a de menor custo total) e liga
    `alerta_capacidade` — sinal de que o custo total deixa de ser o critério
    e o caso pode precisar de orientação adicional (ex.: revisão de
    orçamento), não só escolha de produto.

    Se `capacidade_mensal_maxima` não for informado, comportamento
    inalterado: recomendação por menor custo total apenas — essa dimensão é
    opcional, usada quando o agente conseguir levantar essa informação do
    cliente.

    Ponto de entrada único que o agente conversacional chama para
    recomendar ao cliente.
    """
    resultados = {
        "estrategia_a_parcial_rotativo": estrategia_a_parcial_rotativo(
            fatura_total, valor_disponivel_agora, rolou_mes_anterior
        ),
        "estrategia_b_parcelamento_total": estrategia_b_parcelamento_total(
            fatura_total, n_parcelas_parcelamento, dias_apos_vencimento
        ),
        "estrategia_c_parcial_mais_credito": estrategia_c_parcial_mais_credito(
            fatura_total, valor_disponivel_agora, vinculo, n_parcelas_credito
        ),
        "estrategia_d_adiar_total": estrategia_d_adiar_total(rolou_mes_anterior),
    }

    # Etapa 1: viabilidade.
    viaveis = [
        {"estrategia": nome, "resultado": resultado}
        for nome, resultado in resultados.items()
        if resultado is not None and resultado.get("viavel")
    ]

    def _chave_custo(item: dict) -> float:
        valor = item["resultado"].get("valor_total_final")
        return valor if valor is not None else float("inf")

    viaveis.sort(key=_chave_custo)

    com_custo = [v for v in viaveis if v["resultado"].get("valor_total_final") is not None]
    economia_vs_pior_opcao = (
        com_custo[-1]["resultado"]["valor_total_final"] - com_custo[0]["resultado"]["valor_total_final"]
        if len(com_custo) >= 2
        else 0.0
    )

    if capacidade_mensal_maxima is None:
        return {
            "estrategias_viaveis": viaveis,
            "estrategias_cabem_no_orcamento": viaveis,
            "estrategias_fora_do_orcamento": [],
            "recomendada": viaveis[0] if viaveis else None,
            "economia_vs_pior_opcao": economia_vs_pior_opcao,
            "alerta_capacidade": False,
        }

    # Etapa 2: affordability — só entra em jogo quando o agente já levantou
    # quanto o cliente consegue pagar por mês.
    def _cabe_no_orcamento(item: dict) -> bool:
        parcela = item["resultado"].get("valor_parcela_mensal")
        return parcela is None or parcela <= capacidade_mensal_maxima

    estrategias_cabem_no_orcamento = [v for v in viaveis if _cabe_no_orcamento(v)]
    estrategias_fora_do_orcamento = [v for v in viaveis if not _cabe_no_orcamento(v)]

    if estrategias_cabem_no_orcamento:
        # Já ordenado por custo (herdado de `viaveis`).
        recomendada = estrategias_cabem_no_orcamento[0]
        alerta_capacidade = False
    elif estrategias_fora_do_orcamento:
        # Nenhuma cabe: recomenda a de menor parcela mensal, não a mais
        # barata no total — minimiza o dano de caixa imediato do cliente.
        recomendada = min(
            estrategias_fora_do_orcamento,
            key=lambda item: item["resultado"]["valor_parcela_mensal"],
        )
        alerta_capacidade = True
    else:
        recomendada = None
        alerta_capacidade = False

    resultado_final = {
        "estrategias_viaveis": viaveis,
        "estrategias_cabem_no_orcamento": estrategias_cabem_no_orcamento,
        "estrategias_fora_do_orcamento": estrategias_fora_do_orcamento,
        "recomendada": recomendada,
        "economia_vs_pior_opcao": economia_vs_pior_opcao,
        "alerta_capacidade": alerta_capacidade,
    }
    if alerta_capacidade:
        resultado_final["mensagem_alerta_capacidade"] = (
            f"Mesmo a opção mais barata (parcela mensal de "
            f"R$ {recomendada['resultado']['valor_parcela_mensal']:.2f}) está "
            f"acima da capacidade mensal informada (R$ {capacidade_mensal_maxima:.2f}). "
            "Caso pode precisar de orientação adicional (ex.: revisão de "
            "orçamento), não só escolha de produto."
        )
    return resultado_final


# ---------------------------------------------------------------------------
# Exemplos de uso (validação visual manual)
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    def _mostrar(titulo: str, resultado: dict) -> None:
        print(f"\n=== {titulo} ===")
        for item in resultado["estrategias_viaveis"]:
            print(f"  [{item['estrategia']}] -> {item['resultado']}")
        print(f"  RECOMENDADA: {resultado['recomendada']['estrategia'] if resultado['recomendada'] else None}")
        print(f"  Economia vs pior opção: R$ {resultado['economia_vs_pior_opcao']:.2f}")
        if "alerta_capacidade" in resultado:
            cabem = [i["estrategia"] for i in resultado["estrategias_cabem_no_orcamento"]]
            fora = [i["estrategia"] for i in resultado["estrategias_fora_do_orcamento"]]
            print(f"  Cabem no orçamento: {cabem}")
            print(f"  Fora do orçamento: {fora}")
            print(f"  Alerta de capacidade: {resultado['alerta_capacidade']}")
            if resultado.get("mensagem_alerta_capacidade"):
                print(f"  Mensagem: {resultado['mensagem_alerta_capacidade']}")

    # 1) CLT privado, fatura R$ 3.000, consegue pagar R$ 1.000 agora,
    #    não rolou mês anterior, 5 dias antes do vencimento.
    r1 = comparar_estrategias(
        fatura_total=3000.0,
        valor_disponivel_agora=1000.0,
        vinculo="clt_privado",
        rolou_mes_anterior=False,
        dias_apos_vencimento=-5,
    )
    _mostrar("Exemplo 1: CLT privado, 5 dias antes do vencimento", r1)

    # 2) Aposentado INSS, fatura R$ 5.000, já rolou mês anterior (A e D
    #    ficam bloqueadas), 10 dias após o vencimento (entrada obrigatória).
    r2 = comparar_estrategias(
        fatura_total=5000.0,
        valor_disponivel_agora=500.0,
        vinculo="aposentado_inss",
        rolou_mes_anterior=True,
        dias_apos_vencimento=10,
    )
    _mostrar("Exemplo 2: aposentado INSS, rolou mês anterior, 10 dias após vencimento", r2)

    # 3) Servidor público, fatura R$ 2.000, 20 dias após vencimento
    #    (fora da janela de parcelamento — só sobra crédito/consignado).
    r3 = comparar_estrategias(
        fatura_total=2000.0,
        valor_disponivel_agora=200.0,
        vinculo="servidor_publico",
        rolou_mes_anterior=False,
        dias_apos_vencimento=20,
    )
    _mostrar("Exemplo 3: servidor público, 20 dias após vencimento (fora da janela)", r3)

    # 4) Profissional liberal, fatura alta parcelada em 24x sem entrada —
    #    dispara o teto regulatório de 2x (CMN 5.112, art. 2º-C).
    r4 = comparar_estrategias(
        fatura_total=8000.0,
        valor_disponivel_agora=0.0,
        vinculo="profissional_liberal",
        rolou_mes_anterior=False,
        dias_apos_vencimento=-1,
        n_parcelas_parcelamento=24,
    )
    _mostrar("Exemplo 4: profissional liberal, 24x sem entrada (teto de 2x deve acionar)", r4)

    # 5) Informal, fatura R$ 6.000, já rolou mês anterior (A e D bloqueadas),
    #    10 dias após vencimento, capacidade mensal de R$ 700. A estratégia
    #    mais barata no total (C, crédito pessoal 6x) tem parcela de
    #    ~R$ 1.144 — não cabe. Recomendação deve migrar para B (24x, parcela
    #    ~R$ 487), mesmo sendo mais cara no total.
    r5 = comparar_estrategias(
        fatura_total=6000.0,
        valor_disponivel_agora=0.0,
        vinculo="informal",
        rolou_mes_anterior=True,
        dias_apos_vencimento=10,
        n_parcelas_parcelamento=24,
        n_parcelas_credito=6,
        capacidade_mensal_maxima=700.0,
    )
    _mostrar(
        "Exemplo 5: informal, capacidade mensal R$ 700 — mais barata não cabe, recomendação migra",
        r5,
    )
