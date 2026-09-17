Estou construindo o motor de cálculo determinístico para um agente conversacional 
(Google ADK) que ajuda clientes do Itaú a decidir a melhor forma de lidar com uma 
fatura de cartão que não conseguem pagar integralmente. Preciso de um módulo Python 
puro, testável, SEM nenhuma dependência de LLM/ADK ainda — isso vem depois, como 
tool. Este módulo é o núcleo confiável de cálculo que o agente vai chamar.

PROJETO: /Users/brown/Documents/MLGeral/itau_agente_rotativo/
Criar em: motor_decisao/calculo.py (+ motor_decisao/__init__.py vazio)

CONTEXTO DE NEGÓCIO (regras reais do Itaú, já validadas com fonte oficial):

1. TAXAS OFICIAIS POR PRODUTO (fonte: Banco Central, "histórico de taxa de juros 
   diário", Itaú Unibanco S.A., taxas ao mês, prefixadas):
   - rotativo: 14.70% a.m.
   - parcelamento_fatura: 9.65% a.m.
   - credito_pessoal: 4.00% a.m.
   - consignado_privado: 3.56% a.m.
   - consignado_publico: 1.73% a.m.
   - consignado_inss: 1.82% a.m.
   Definir essas taxas como um dict de constantes nomeado TAXAS_MENSAIS, com 
   comentário citando a fonte (BC, planilha oficial) para cada uma.

2. ELEGIBILIDADE POR VÍNCULO EMPREGATÍCIO (enum ou Literal em Python):
   - "servidor_publico" → elegível a consignado_publico (+ credito_pessoal como 
     fallback)
   - "aposentado_inss" → elegível a consignado_inss (+ credito_pessoal)
   - "clt_privado" → elegível a consignado_privado, SE tiver convênio com a 
     empresa (campo booleano separado `tem_convenio_consignado`); se não tiver 
     convênio, só credito_pessoal
   - "profissional_liberal" → só credito_pessoal (sem acesso a consignado)
   - "informal" → só credito_pessoal (sem acesso a consignado)
   Escrever uma função `taxa_credito_disponivel(vinculo: str, tem_convenio: 
   bool = False) -> tuple[str, float]` que retorna (nome_do_produto, 
   taxa_mensal) — a MELHOR taxa de crédito pessoal/consignado disponível para 
   aquele vínculo. Se vínculo inválido/desconhecido, levantar ValueError com 
   mensagem clara.

3. REGRA DOS "2 MESES SEGUIDOS" (Itaú, confirmada em fonte oficial): se o 
   cliente já pagou um valor entre o mínimo e o total da fatura (ou seja, usou 
   o rotativo) no ciclo anterior, ele NÃO pode fazer isso de novo neste ciclo — 
   o sistema forçaria entrada de 5% a 20% + parcelamento automático. Isso 
   significa: a "Estratégia A" (pagar parcial + resto no rotativo) só é viável 
   se `rolou_rotativo_mes_anterior == False`.

4. JANELA DE CONTRATAÇÃO DO PARCELAMENTO (regras Itaú):
   - Antes da data de vencimento: as duas modalidades disponíveis — "Entrada + 
     parcelas fixas" (entrada mínima 5% a 20% do saldo + 4x-24x) e "Parcela 
     Fixa" sem entrada (4x-24x)
   - Até 15 dias após o vencimento: só "Entrada + parcelas fixas" ainda pode 
     ser contratada
   - Mais de 15 dias após o vencimento: nenhuma das duas — fora do escopo 
     deste motor (cai em módulo de renegociação, não implementado aqui)

5. TETO REGULATÓRIO (CMN 5.112, art. 2º-C): em qualquer renegociação/parcela-
   mento, o valor total cobrado a título de juros e encargos não pode exceder 
   o valor original da dívida — ou seja, o valor final nunca pode ultrapassar 
   2x o valor original. Toda função de cálculo de parcelamento deve aplicar 
   esse teto como cap no resultado final, e retornar um flag 
   `teto_aplicado: bool` indicando se o teto precisou ser acionado.

FUNÇÕES A IMPLEMENTAR (todas com type hints, docstring em português explicando 
a regra de negócio por trás, e nenhuma delas deve imprimir nada — só retornar 
dados estruturados):

a) `calcular_juros_compostos(valor: float, taxa_mensal_pct: float, 
   n_parcelas: int) -> float`
   Cálculo padrão de amortização (sistema Price) para o valor total das 
   parcelas com juros. Função auxiliar usada pelas estratégias B e C.

b) `estrategia_a_parcial_rotativo(fatura_total: float, valor_pago_agora: 
   float, rolou_mes_anterior: bool) -> dict | None`
   Pagar parcial agora + resto no rotativo por 1 ciclo (1 mês). Retorna None 
   se `rolou_mes_anterior=True` (estratégia inviável, regra dos 2 meses). 
   Senão, retorna dict com: valor_pago_agora, valor_remanescente, 
   taxa_aplicada, custo_juros, valor_total_final, viavel=True.

c) `estrategia_b_parcelamento_total(fatura_total: float, n_parcelas: int, 
   dias_apos_vencimento: int, valor_entrada: float | None = None) -> dict`
   Parcela a fatura inteira. Se `dias_apos_vencimento <= 0` (antes/no 
   vencimento): aceita com ou sem entrada (Parcela Fixa se entrada=None, 
   Entrada+parcelas se entrada for informado, validando que entrada está 
   entre 5% e 20% do saldo — senão ValueError). Se `0 < dias_apos_vencimento 
   <= 15`: exige entrada (calcula uma entrada mínima de 5% se não informada). 
   Se `dias_apos_vencimento > 15`: retorna dict com viavel=False e motivo. 
   Validar n_parcelas entre 4 e 24 (ValueError fora disso). Aplicar o teto de 
   2x do item 5. Retornar dict com: valor_entrada, valor_parcelado, 
   valor_parcela, custo_juros_total, valor_total_final, teto_aplicado, 
   viavel.

d) `estrategia_c_parcial_mais_credito(fatura_total: float, valor_pago_agora: 
   float, vinculo: str, tem_convenio: bool, n_parcelas_credito: int) -> dict`
   Paga parcial agora + usa crédito pessoal/consignado (conforme elegibilidade 
   do vínculo, via `taxa_credito_disponivel`) só para cobrir o restante 
   (fatura_total - valor_pago_agora), parcelado em n_parcelas_credito, 
   aplicando juros compostos (função a). Retorna dict com: valor_pago_agora, 
   valor_financiado, produto_usado, taxa_aplicada, custo_juros, 
   valor_total_final, viavel=True.

e) `estrategia_d_adiar_total(rolou_mes_anterior: bool) -> dict | None`
   Retorna None se `rolou_mes_anterior=True` (mesma regra da estratégia A — 
   sem viabilidade de adiar de novo). Senão, retorna um dict simples indicando 
   viabilidade e um aviso de que essa é a opção mais arriscada (sem cálculo de 
   custo, já que "adiar" não tem valor definido até o próximo ciclo).

f) `comparar_estrategias(fatura_total: float, valor_disponivel_agora: float, 
   vinculo: str, rolou_mes_anterior: bool, dias_apos_vencimento: int, 
   tem_convenio: bool = False, n_parcelas_parcelamento: int = 12, 
   n_parcelas_credito: int = 12) -> dict`
   Função orquestradora principal — chama as estratégias A, B, C, D conforme 
   os parâmetros, filtra as que retornarem viavel=False ou None, ordena as 
   viáveis por `valor_total_final` (crescente) e retorna um dict com: 
   `estrategias_viaveis` (lista ordenada, cada item com o nome da estratégia + 
   o dict de resultado), `recomendada` (a primeira da lista), 
   `economia_vs_pior_opcao` (diferença entre a melhor e a pior estratégia 
   viável, em R$). Esta é a função que o agente vai chamar diretamente.

REQUISITOS GERAIS:
- Código e docstrings em português, comentários explicando a regra de negócio 
  (não só o "o quê", mas o "porquê" de cada regra, citando a fonte quando fizer 
  sentido — ex: "regra dos 2 meses, confirmada em itau.com.br/.../pagamento-
  minimo")
- Validação de entrada rigorosa (valores negativos, n_parcelas fora de 4-24, 
  vínculo desconhecido) — sempre ValueError com mensagem clara, nunca falha 
  silenciosa
- NENHUMA chamada de rede, banco de dados, ou LLM neste módulo — é lógica pura
- Incluir ao final do arquivo um bloco `if __name__ == "__main__":` com 3-4 
  exemplos de uso reais (ex: cliente CLT sem convênio, fatura R$ 3.000, 
  consegue pagar R$ 1.000 agora, não rolou mês anterior, 5 dias antes do 
  vencimento — mostrar o resultado de `comparar_estrategias` formatado de 
  forma legível no console) para eu conseguir rodar e validar visualmente 
  antes de integrar ao agente
- Escrever também motor_decisao/testes_calculo.py com pytest cobrindo pelo 
  menos: a regra dos 2 meses bloqueando estratégias A e D, o teto de 2x sendo 
  aplicado corretamente, a elegibilidade de cada vínculo retornando o produto 
  certo, e um caso onde `comparar_estrategias` ordena corretamente 3+ 
  estratégias por custo