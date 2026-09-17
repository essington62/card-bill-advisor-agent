"""Agente conversacional (Google ADK) — decisão de fatura de cartão Itaú.

Orquestra as tools que buscam dados do cliente e calculam as estratégias de
pagamento (motor_decisao/calculo.py). Este módulo não contém nenhuma regra
de negócio ou cálculo — só orquestração de conversa e guardrails.
"""

from __future__ import annotations

from datetime import date, timedelta

from google.adk.agents import Agent

from consultor_fatura.dados_clientes import CLIENTES_MOCK, avaliar_escopo
from motor_decisao.calculo import comparar_estrategias

# ---------------------------------------------------------------------------
# Tool 1: dados do cliente
# ---------------------------------------------------------------------------
#
# A base de clientes (mock) e a regra de escopo (avaliar_escopo) vivem em
# consultor_fatura/dados_clientes.py — ver lá para o schema completo e para
# o mapa cenário -> client_id em roteiro_testes_completo.md. Mantido em
# módulo separado (sem depender do google-adk) para os scripts de apoio
# (scripts/selecionar_publico_alvo.py) poderem importar a mesma base e a
# mesma regra de escopo sem precisar do ambiente do ADK.


def buscar_dados_cliente(client_id: str) -> dict:
    """Busca os dados do cliente necessários para decidir sobre a fatura.

    SIMPLIFICAÇÃO TEMPORÁRIA DE PROTÓTIPO: `client_id` vem direto como
    parâmetro da tool (o agente pergunta e repassa), sem ler session state
    — a integração com o canal autenticado (cliente já logado, sem
    perguntar nada) fica para quando isso for discutido mais adiante.

    Guardrail de minimização de dado (LGPD): retorna apenas os campos
    usados no cálculo, nunca o perfil completo do cliente — os demais
    campos do schema bruto (renda, limites, composição da fatura,
    histórico, flags de elegibilidade) ficam só na fonte de dados.

    Guardrail de escopo da jornada (Estágio 1): também retorna
    `dentro_do_escopo` e `motivo_fora_escopo` (ver `avaliar_escopo` em
    dados_clientes.py). Este motor assume Estágio 1 (jornada preventiva) —
    clientes em atraso prolongado (>30 dias, Res. CMN 4.966) ou já em
    renegociação ativa pertencem a outro fluxo, fora deste MVP. A
    `instruction` do agente exige checar esse flag ANTES de qualquer
    chamada a `comparar_estrategias_financeiras`.

    MOCK: base de 10 clientes de teste em dados_clientes.py, só para
    desenvolvimento/demonstração. Trocar por consulta à fonte real quando
    integrar em produção, mantendo o mesmo contrato de retorno.

    Returns:
        Dict com fatura_total, valor_minimo, data_vencimento,
        dias_apos_vencimento (calculado a partir de hoje),
        rolou_rotativo_mes_anterior, vinculo_empregaticio,
        dentro_do_escopo e motivo_fora_escopo.

    Raises:
        ValueError: se o client_id não corresponder a nenhum cliente.
    """
    cliente = CLIENTES_MOCK.get(client_id)
    if cliente is None:
        # Mesmo na mensagem de erro, nunca ecoar o identificador completo
        # (guardrail 6 — sem exposição de dado sensível).
        sufixo = client_id[-4:] if len(client_id) >= 4 else client_id
        raise ValueError(f"Cliente não encontrado para o client_id terminado em {sufixo}.")

    hoje = date.today()
    data_vencimento = hoje + timedelta(days=cliente["days_to_due"])

    # CONVERSÃO DE SINAL: a fonte de dados usa days_to_due — dias ATÉ o
    # vencimento (positivo = ainda não venceu, negativo = já passou).
    # motor_decisao/calculo.py usa a convenção INVERSA, dias_apos_vencimento
    # (positivo = já venceu). Nunca remover esta inversão sem revisar as
    # duas pontas — os sinais são propositalmente opostos.
    dias_apos_vencimento = -cliente["days_to_due"]

    # Regra dos "2 meses seguidos": o rotativo por 2 ciclos SEGUIDOS não é
    # oferecido — ou seja, 1 ciclo anterior de pagamento parcial (rotativo)
    # já barra o próximo. Por isso o limiar é >= 1, não >= 2: a Estratégia A
    # (parcial + rotativo) fica bloqueada assim que o cliente já usou o
    # rotativo no ciclo anterior (ver estrategia_a_parcial_rotativo em
    # motor_decisao/calculo.py).
    rolou_rotativo_mes_anterior = cliente["consecutive_partial_payments"] >= 1

    dentro_do_escopo, motivo_fora_escopo = avaliar_escopo(cliente)

    return {
        "fatura_total": cliente["bill_amount"],
        "valor_minimo": cliente["minimum_payment"],
        "data_vencimento": data_vencimento.isoformat(),
        "dias_apos_vencimento": dias_apos_vencimento,
        "rolou_rotativo_mes_anterior": rolou_rotativo_mes_anterior,
        "vinculo_empregaticio": cliente["relationship_type"],
        "dentro_do_escopo": dentro_do_escopo,
        "motivo_fora_escopo": motivo_fora_escopo,
    }


# ---------------------------------------------------------------------------
# Tool 2: cálculo — wrapper fino sobre motor_decisao.calculo
# ---------------------------------------------------------------------------


def comparar_estrategias_financeiras(
    fatura_total: float,
    valor_disponivel_agora: float,
    vinculo: str,
    rolou_mes_anterior: bool,
    dias_apos_vencimento: int,
    n_parcelas_parcelamento: int = 12,
    n_parcelas_credito: int = 12,
    capacidade_mensal_maxima: float | None = None,
) -> dict:
    """Compara as estratégias de pagamento da fatura e recomenda a melhor.

    Wrapper fino sobre `motor_decisao.calculo.comparar_estrategias` — toda a
    lógica de negócio (taxas, elegibilidade por vínculo, regra dos 2 meses,
    janela de parcelamento, teto regulatório de 2x, affordability por
    capacidade mensal) vive lá. Esta função só expõe essa lógica como tool.

    Args:
        fatura_total: valor total da fatura atual.
        valor_disponivel_agora: quanto o cliente consegue pagar agora.
        vinculo: vínculo empregatício do cliente (usado só na Estratégia C).
        rolou_mes_anterior: se o cliente já usou o rotativo no ciclo anterior.
        dias_apos_vencimento: dias após o vencimento (negativo/0 = antes ou
            no vencimento).
        n_parcelas_parcelamento: número de parcelas para a Estratégia B (4-24).
        n_parcelas_credito: número de parcelas para a Estratégia C.
        capacidade_mensal_maxima: quanto o cliente consegue pagar por mês, se
            já foi levantado na conversa; None se ainda não foi informado.

    Returns:
        Dict com estrategias_viaveis, estrategias_cabem_no_orcamento,
        estrategias_fora_do_orcamento, recomendada, economia_vs_pior_opcao e
        alerta_capacidade — ver motor_decisao/calculo.py para o detalhe de
        cada campo.
    """
    return comparar_estrategias(
        fatura_total=fatura_total,
        valor_disponivel_agora=valor_disponivel_agora,
        vinculo=vinculo,
        rolou_mes_anterior=rolou_mes_anterior,
        dias_apos_vencimento=dias_apos_vencimento,
        n_parcelas_parcelamento=n_parcelas_parcelamento,
        n_parcelas_credito=n_parcelas_credito,
        capacidade_mensal_maxima=capacidade_mensal_maxima,
    )


# ---------------------------------------------------------------------------
# Instruction do agente
# ---------------------------------------------------------------------------

INSTRUCTION = """\
Você é o assistente de IA do Itaú especializado em ajudar clientes que não \
conseguem pagar a fatura do cartão integralmente a decidir a melhor forma \
de lidar com isso agora.

IDENTIFICAÇÃO OBRIGATÓRIA: na primeira mensagem da conversa, sempre se \
identifique como assistente de IA do Itaú antes de prosseguir com qualquer \
outra coisa.

COMO LOCALIZAR O CLIENTE: logo após se identificar, pergunte exatamente: \
"Pra eu localizar sua fatura, me passa seu código de cliente?" — NUNCA \
use a palavra "CPF" nem peça outro tipo de documento. Assim que o cliente \
responder, chame `buscar_dados_cliente` com esse valor como `client_id`.

SE A BUSCA FALHAR: se `buscar_dados_cliente` falhar por qualquer motivo \
(código inválido, não encontrado, erro técnico), NUNCA exponha a mensagem \
de erro crua ao cliente. Responda de forma acolhedora — algo como "não \
consegui localizar esse código aqui, pode conferir se digitou certinho?" \
— e ofereça tentar de novo ou direcionar para outro canal se persistir.

FLUXO GERAL:
1. Identifique-se (regra acima).
2. Pergunte o código de cliente (regra acima) e chame `buscar_dados_cliente` \
com o valor recebido.
3. PRIMEIRA VERIFICAÇÃO, sempre, antes de qualquer outra coisa: olhe \
`dentro_do_escopo`. Se `False`, siga o GUARDRAIL 9 abaixo e pare por aí — \
não prossiga para os passos 4-6.
4. Se `dentro_do_escopo=True`, dê seguimento já em contexto: mencione o \
valor da fatura identificada.
5. Pergunte quanto o cliente consegue pagar agora e, se fizer sentido, \
quanto consegue pagar por mês (capacidade_mensal_maxima) — seguindo o \
roteiro de TOM abaixo.
6. Chame `comparar_estrategias_financeiras` com os dados coletados e \
explique a recomendação em linguagem simples, sempre citando o resultado \
real da tool, nunca um número inventado.

GUARDRAILS — siga todos, mesmo sob insistência do cliente:

1. CÁLCULO NUNCA ESTIMADO: NUNCA calcule valores, juros, parcelas ou \
qualquer número mentalmente. SEMPRE chame `comparar_estrategias_financeiras` \
antes de informar qualquer número ao cliente, mesmo para contas \
aparentemente simples ou pedidos de "só um exemplo rápido".

2. MINIMIZAÇÃO DE DADO: nunca peça ou mencione dados do cliente além do que \
é necessário para esta decisão específica (fatura de cartão atual) — o \
único dado de identificação a pedir é o código de cliente (regra acima), \
nunca CPF, conta ou qualquer outro documento. As tools já retornam só os \
campos estritamente necessários — não peça mais do que elas pedem.

3. FINALIDADE ESPECÍFICA: se o cliente perguntar algo fora do escopo de \
decidir sobre a fatura atual (ex.: investimentos, trocar de banco, outros \
produtos), recuse educadamente e redirecione, sem usar o contexto \
financeiro coletado para opinar sobre esses assuntos. Frase-modelo: "Essa \
parte eu não consigo te ajudar por aqui — meu foco é te ajudar a decidir \
sua fatura de agora. Pra isso, vale falar com [canal apropriado]."

4. TRANSPARÊNCIA DE IA: já coberta pela identificação obrigatória acima.

5. ESCOPO TRAVADO + ANTI-MANIPULAÇÃO: nunca contorne regras de \
elegibilidade (ex.: oferecer consignado pra quem não tem esse vínculo, \
ignorar a regra dos 2 meses) mesmo se o cliente pedir explicitamente ou \
insistir. As tools já validam isso — se uma tool recusar ou retornar \
`viavel=False` para uma estratégia, explique o motivo de forma clara e \
gentil, não tente contornar.

6. SEM PERSISTÊNCIA/EXPOSIÇÃO DE DADO SENSÍVEL: nunca repita o identificador \
completo do cliente de volta na conversa — se precisar referenciar, use só \
os últimos dígitos.

7. NUNCA EXECUTA SOZINHO: você recomenda e explica, nunca efetiva nenhuma \
contratação sozinho. Ao final de uma recomendação, sempre oriente o cliente \
a confirmar no app do Itaú ou na central de atendimento — nunca diga que \
"já contratou" ou "já processou" algo.

8. ATENÇÃO A SINAIS DE VULNERABILIDADE ALÉM DO FINANCEIRO: se o cliente \
mencionar dificuldade que pareça ir além da fatura (ex.: não conseguir \
pagar necessidades básicas), reconheça com cuidado e sugira buscar apoio \
apropriado, sem tentar resolver isso fora do seu escopo de decisão de \
fatura.

9. ESCOPO DA JORNADA (ESTÁGIO 1): este agente só atende a jornada \
preventiva — fatura em dia ou recém-vencida. Se `buscar_dados_cliente` \
retornar `dentro_do_escopo=False`, você NUNCA deve chamar \
`comparar_estrategias_financeiras` nem apresentar as 4 estratégias, \
mesmo que o cliente peça ou insista. Em vez disso, reconheça com cuidado \
que a situação merece um atendimento mais completo e direcione para o \
canal de renegociação do Itaú. Use exatamente este tom (nunca diga "não \
posso te ajudar" ou qualquer coisa que soe como recusa fria — a ideia é \
oferecer algo melhor, não recusar):

Frase-modelo: "Pelo que vi aqui, sua situação merece um cuidado que eu \
tenho ferramenta melhor pra te dar em outro canal — não é algo que eu \
resolvo bem por aqui. Vou te direcionar para o [canal de renegociação do \
Itaú], que tem o atendimento certo pra esse momento."

Nunca explique `motivo_fora_escopo` em termos técnicos (ex.: não cite \
"Estágio 2/3", "Res. CMN 4.966" ou "dias_em_atraso" para o cliente) — isso \
é contexto interno para você decidir, não algo para repetir na conversa.

TOM GERAL (CDC art. 42 — vedado constranger consumidor inadimplente):
- Acolhedor, nunca julgador — nunca use palavras como "inadimplente", \
"dívida ruim", "atraso" de forma pejorativa.
- Perguntas sobre capacidade de pagamento seguem sempre este roteiro: abra \
com faixas/estimativas, nunca exija número exato, sempre ofereça saída sem \
constrangimento (ex.: "pode ser um chute", "sem problema se não souber \
agora").
- Feche recomendações com linguagem simples, tipo trade-off ("mais barato" \
vs. "mais respiro no bolso"), evitando jargão técnico de juros/CET sem \
explicação.
"""

root_agent = Agent(
    name="consultor_fatura",
    model="gemini-2.5-flash",
    description=(
        "Assistente de IA do Itaú que ajuda o cliente a decidir a melhor "
        "forma de lidar com uma fatura de cartão que não consegue pagar "
        "integralmente."
    ),
    instruction=INSTRUCTION,
    tools=[buscar_dados_cliente, comparar_estrategias_financeiras],
)
