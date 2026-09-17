# Card Bill Advisor Agent

Agente conversacional (Google ADK + Gemini) que ajuda clientes do Itaú a
decidir a melhor forma de lidar com uma fatura de cartão de crédito que não
conseguem pagar integralmente — comparando rotativo, parcelamento de
fatura, crédito pessoal e consignado, com taxas oficiais do Banco Central.

Construído para a **Batalha de Agentes** (hackathon Itaú x Google Cloud).

## O problema

Quando o cliente não paga a fatura integral, ele normalmente vê apenas as
opções que o próprio app oferece (parcelar ou cair no rotativo) — sem
comparação real de custo contra outras alternativas do próprio banco
(crédito pessoal, consignado). O agente resolve isso: entende a situação
do cliente, calcula todas as estratégias viáveis com transparência, e
recomenda a mais barata dentro do que ele consegue pagar.

## Arquitetura

- **`consultor_fatura/`** — agente ADK (orquestração de conversa, tools,
  guardrails). Não contém nenhuma regra de negócio ou cálculo.
- **`motor_decisao/`** — motor de cálculo determinístico (Python puro, sem
  dependência de LLM): taxas, elegibilidade por vínculo, regra dos "2
  meses seguidos", janela de parcelamento, teto regulatório de 2x (CMN
  5.112), comparação de estratégias por custo e por capacidade de
  pagamento mensal.
- **`data/itau.csv` + `consultor_fatura/config/clientes.yml`** — fonte de
  dados dos clientes, com mapeamento de colunas configurável (trocar
  `path`/`columns` no YAML para apontar para uma base diferente, sem
  mexer em código).

Princípio central: **o Gemini nunca calcula nada sozinho** — todo número
(taxa, juros, valor final) vem do motor determinístico via function
calling. O modelo só conduz a conversa, decide quando chamar cada tool, e
traduz o resultado em linguagem simples.

## As 4 estratégias comparadas

| Estratégia | Descrição |
|---|---|
| A — Parcial + rotativo | Paga parte agora, resto no rotativo por 1 ciclo |
| B — Parcelamento de fatura | Parcela o valor total (4x-24x, com/sem entrada conforme janela de vencimento) |
| C — Parcial + crédito complementar | Paga parte agora + financia só o restante via CP ou consignado (conforme elegibilidade) |
| D — Adiar | Só viável se ainda não usou rotativo no ciclo anterior |

Taxas oficiais (Banco Central, Itaú Unibanco S.A.):

| Produto | Taxa mensal |
|---|---|
| Rotativo | 14,70% |
| Parcelamento de fatura | 9,65% |
| Crédito Pessoal | 4,00% |
| Consignado privado | 3,56% |
| Consignado público | 1,73% |
| Consignado INSS | 1,82% |

## Base regulatória

- **Res. CMN 4.549/2017 + CMN 5.112/2023** — mecânica do rotativo e
  parcelamento de fatura
- **Res. CMN 4.966/2021** — estágios de risco de crédito (o agente só
  atende a jornada preventiva, Estágio 1)
- **CMN 5.112, art. 2º-C** — teto de juros (valor final ≤ 2x o original)
- **MP nº 1.292/2025 (Crédito do Trabalhador)** — CLT privado acessa
  consignado diretamente, sem convênio com a empresa
- **CDC art. 42** — vedado constranger consumidor inadimplente (base do
  tom acolhedor do agente)
- **LGPD** — minimização de dado, finalidade específica

## Guardrails implementados

1. Cálculo nunca estimado pelo LLM (sempre via tool)
2. Minimização de dado (só os campos necessários saem do motor de busca)
3. Finalidade específica (recusa perguntas fora do escopo da fatura atual)
4. Transparência de IA (identificação obrigatória no início da conversa)
5. Anti-manipulação (nunca contorna regras de elegibilidade, mesmo sob insistência)
6. Sem exposição de identificador completo do cliente
7. Nunca executa transação sozinho — sempre direciona para canal oficial
8. Atenção a sinais de vulnerabilidade além do financeiro
9. Escopo de jornada — clientes fora do Estágio 1 (atraso >30 dias ou
   renegociação ativa) são redirecionados, nunca calculados por este motor

## Como rodar

```bash
conda activate itau_agent
cd consultor_fatura
adk web
```

Abra a URL local indicada no terminal, selecione o app `consultor_fatura`
e informe um dos códigos de cliente de teste (ver
`consultor_fatura/roteiro_testes_completo.md`).

## Testes

```bash
cd motor_decisao
pytest testes_calculo.py -v
```

Roteiro de testes funcionais (10 clientes cobrindo a árvore de decisão) em
`consultor_fatura/roteiro_testes_completo.md`; roteiro de guardrails em
`consultor_fatura/testes_guardrails.md`.

## Status

Protótipo (MVP) desenvolvido para o hackathon. Simplificações conhecidas
documentadas nos arquivos de teste — principal delas: identificação do
cliente via código informado no chat, no lugar de integração com canal
autenticado (a ser discutida em produção).
