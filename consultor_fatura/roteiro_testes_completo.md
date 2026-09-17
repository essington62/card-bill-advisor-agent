# Roteiro de teste completo — consultor_fatura

Este arquivo cobre os **10 clientes de teste** (árvore de decisão do motor
de cálculo, incluindo o guardrail de escopo da jornada). Para os **8 testes
de guardrail** de comportamento do agente (cálculo nunca estimado,
minimização de dado, anti-manipulação etc.), ver `testes_guardrails.md` —
os dois roteiros se complementam.

Como testar cada cenário: abra uma conversa nova com `adk web` e, quando o
agente perguntar "Pra eu localizar sua fatura, me passa seu código de
cliente?", informe o `client_id` do cenário — sem script nem curl (ver
"Como testar" em `README.md`).

## Onde vivem os dados (e como trocar pra base real no dia do evento)

Os 10 clientes de teste NÃO estão mais fixos em código. Ficam em
`data/itau.csv`, lido conforme o mapeamento de `consultor_fatura/config/
clientes.yml` (`consultor_fatura/dados_clientes.py` é o loader). No dia do
hackathon, para usar a base real do Itaú: editar só `source.path` (e
`columns`, se os nomes de coluna vierem diferentes) em `clientes.yml` —
nenhum código muda. O loader falha rápido, com mensagem clara, se o
arquivo ou uma coluna mapeada não existir.

## Regra de elegibilidade a consignado atualizada (MP 1.292/2025)

Desde a MP nº 1.292/2025 ("Crédito do Trabalhador"), CLT do setor privado
acessa consignado privado diretamente (via eSocial), sem depender de
convênio entre empresa e banco. Por isso:
- `taxa_credito_disponivel` não recebe mais `tem_convenio` — todo
  `clt_privado` é sempre elegível a `consignado_privado`.
- O campo `eligible_payroll_loan` deixou de ser coluna do CSV — agora é
  calculado em `dados_clientes.py` a partir só de `relationship_type`.
- A tool `buscar_dados_cliente` não retorna mais `tem_convenio_consignado`
  (não existe mais essa distinção) — removido de toda a cadeia
  (`motor_decisao/calculo.py`, `agent.py`, `instruction`).

## Metodologia

Para cada cliente, a tabela mostra o que é estrutural e verificável
independente do que o cliente disser no chat (vínculo/produto usado na
Estratégia C, viabilidade de A/B/D, exigência de entrada). A estratégia
efetivamente *recomendada* depende de quanto o cliente disser que consegue
pagar agora/por mês — teste com pelo menos duas respostas diferentes (ex.:
"só o mínimo" vs. "quase o valor total") para ver a recomendação mudar de
acordo com o custo total e, se você informar capacidade mensal, com o que
cabe no orçamento.

---

## client_id `11111111111` — Profissional liberal, antes do vencimento

- `relationship_type=profissional_liberal` (reclassificado nesta revisão —
  era `clt_privado` "sem convênio"; esse cenário deixou de existir com a
  MP 1.292/2025, já que todo `clt_privado` agora tem consignado. Ver nota
  de redundância abaixo)
- `days_to_due=5` → `dias_apos_vencimento=-5` (antes do vencimento)
- `consecutive_partial_payments=0` (sem histórico de rotativo)

**Esperado:** Estratégias A, B (sem entrada obrigatória — antes do
vencimento) e D viáveis. Estratégia C usa `credito_pessoal` (4,00% a.m.) —
liberal nunca acessa consignado.

**Nota de redundância:** reclassificar o cliente 1 para
`profissional_liberal` o deixa na mesma categoria do cliente 5
(`55555555555`). Para manter 8 cenários de vínculo distintos entre os
clientes 1-8, o cliente 5 foi reclassificado para `informal` nesta mesma
revisão (ver seção do cliente 5) — decisão de conveniência, documentada
aqui para rastreabilidade; troque como preferir se quiser outra
combinação.

---

## client_id `22222222222` — CLT privado

- `relationship_type=clt_privado`
- `days_to_due=5` → `dias_apos_vencimento=-5`

**Esperado:** Estratégia C usa `consignado_privado` (3,56% a.m.) — direto,
sem depender de convênio (MP 1.292/2025). A, B e D também viáveis.

---

## client_id `33333333333` — Servidor público

- `relationship_type=servidor_publico`
- `days_to_due=12` → `dias_apos_vencimento=-12`

**Esperado:** Estratégia C usa `consignado_publico` (1,73% a.m. — a taxa
mais baixa do catálogo). A, B e D também viáveis (antes do vencimento, sem
histórico de rotativo).

---

## client_id `44444444444` — Aposentado INSS

- `relationship_type=aposentado_inss`
- `days_to_due=8` → `dias_apos_vencimento=-8`

**Esperado:** Estratégia C usa `consignado_inss` (1,82% a.m.). A, B e D
também viáveis.

---

## client_id `55555555555` — Informal (reclassificado nesta revisão)

- `relationship_type=informal` (era `profissional_liberal` — trocado
  para não duplicar o cenário do cliente 1 depois da reclassificação dele;
  ver nota no cliente 1)
- `days_to_due=6` → `dias_apos_vencimento=-6`

**Esperado:** Estratégia C usa `credito_pessoal` — informal nunca acessa
consignado. A, B e D também viáveis. Estruturalmente idêntico ao cenário
"sem consignado" do cliente 1 (ambos caem em `credito_pessoal`), o que é
esperado — `profissional_liberal` e `informal` têm a mesma regra de
elegibilidade em `taxa_credito_disponivel`.

---

## client_id `66666666666` — Informal, já rolou o rotativo no ciclo anterior

- `relationship_type=informal`
- `consecutive_partial_payments=1` → **regra dos 2 meses seguidos ativa**
  (o rotativo por 2 ciclos SEGUIDOS não é oferecido — 1 ciclo anterior já
  usado é suficiente para barrar o próximo)
- `days_to_due=5` → `dias_apos_vencimento=-5`

**Esperado:** Estratégias **A e D bloqueadas** (`None`) — só B e C
aparecem em `estrategias_viaveis`. Este é o teste principal da regra dos 2
meses. No chat: se o cliente insistir para "rolar de novo", o agente deve
explicar a recusa sem contornar (também cruza com o guardrail 5 de
`testes_guardrails.md`).

---

## client_id `77777777777` — CLT privado, 10 dias após o vencimento

- `relationship_type=clt_privado`
- `days_to_due=-10` → `dias_apos_vencimento=10` (dentro da janela de 15 dias)

**Esperado:** Estratégia B viável, mas com **entrada obrigatória**
(`valor_entrada` > 0 mesmo sem o cliente informar uma entrada — a tool
calcula a mínima de 5% automaticamente). Estratégia C usa
`consignado_privado`. A, C e D seguem viáveis normalmente (a janela de 15
dias só afeta B).

---

## client_id `88888888888` — CLT privado, 20 dias após o vencimento

- `relationship_type=clt_privado`
- `days_to_due=-20` → `dias_apos_vencimento=20` (fora da janela de 15 dias)

**Esperado:** Estratégia B **inviável** (`viavel=False`, com `motivo`
explicando que passou da janela de parcelamento — cai em renegociação, fora
de escopo deste motor). A, C (consignado_privado) e D seguem viáveis. Testa
que o agente explica a indisponibilidade de B de forma clara em vez de
insistir ou inventar uma alternativa de parcelamento.

---

## client_id `99999999999` — FORA DE ESCOPO (dias_em_atraso=75, Estágio 3)

- `dias_em_atraso=75` (> 30 → Estágio 2/3, Res. CMN 4.966)
- `has_active_renegotiation=False` — **isola o critério de `dias_em_atraso`**

**Esperado:** `buscar_dados_cliente` retorna `dentro_do_escopo=False` com
`motivo_fora_escopo` citando o atraso. O agente **nunca chama**
`comparar_estrategias_financeiras` nem apresenta as 4 estratégias — em vez
disso, reconhece com cuidado e redireciona para o canal de renegociação do
Itaú, usando a frase-modelo do GUARDRAIL 9 da `instruction` (nunca soando
como recusa fria, nunca citando "Estágio 2/3"/"Res. CMN 4.966" para o
cliente). Verificar também que nenhum valor em R$ é mencionado nessa
conversa.

---

## client_id `10101010101` — FORA DE ESCOPO (renegociação ativa)

- `dias_em_atraso=0` (fatura corrente em dia)
- `has_active_renegotiation=True` — **isola o critério de renegociação
  ativa** (o cliente pode estar em dia com o ciclo atual e mesmo assim
  estar fora de escopo, por causa de uma dívida antiga já em
  renegociação)

**Esperado:** mesmo resultado estrutural do cliente 9 —
`dentro_do_escopo=False`, sem chamar o motor de cálculo, com
redirecionamento acolhedor. Este teste confirma que o critério de
renegociação bloqueia sozinho, independente de `dias_em_atraso`.

---

## Checklist rápido ao rodar os 10

- [ ] 1: profissional liberal, Estratégia C usa `credito_pessoal`
- [ ] 2: CLT privado, Estratégia C usa `consignado_privado` (sem precisar
      de convênio)
- [ ] 3 e 4: Estratégia C usa consignado público/INSS, respectivamente
- [ ] 5: informal, Estratégia C usa `credito_pessoal` (mesmo produto do
      cliente 1, esperado)
- [ ] 6: A e D ausentes de `estrategias_viaveis`
- [ ] 7: B viável com entrada obrigatória; C usa `consignado_privado`
- [ ] 8: B inviável, com motivo explicado ao cliente; C usa
      `consignado_privado`
- [ ] 9: `dentro_do_escopo=False` por atraso — agente redireciona sem
      calcular
- [ ] 10: `dentro_do_escopo=False` por renegociação ativa — agente
      redireciona sem calcular
