"""Base de clientes (fonte configurável) + regra de escopo da jornada (Estágio 1).

Módulo deliberadamente SEM dependência do google-adk nem do pandas: tanto a
tool `buscar_dados_cliente` (agent.py) quanto os scripts de apoio
(scripts/selecionar_publico_alvo.py) importam daqui, sem precisar do
ambiente do ADK instalado só para ler/filtrar a base de clientes. Usa só
`csv` (stdlib) + `PyYAML`.

Mesmo padrão de adapter de dados do projeto irmão (cartao_base_taiwan/src/
adapter.py): YAML de config aponta pra fonte + mapeamento de colunas,
fail-fast com mensagem clara se arquivo ou coluna mapeada não existir.

No dia do hackathon, trocar `source.path` (e `columns`, se os nomes vierem
diferentes) em `config/clientes.yml` para apontar pra base real do Itaú —
sem tocar em nenhum código deste módulo.

Cada client_id do mock atual cobre um ramo específico da árvore de decisão
do motor de cálculo — ver consultor_fatura/roteiro_testes_completo.md para
o mapa completo de cenário -> client_id -> resultado esperado.

`days_to_due` é guardado como delta relativo a "hoje" (não como data fixa)
para o mock continuar coerente com o calendário real enquanto o projeto for
demonstrado, sem precisar editar valores com o passar do tempo — quem
consome resolve isso para uma data real no momento do uso.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml

_MODULO_DIR = Path(__file__).resolve().parent  # .../consultor_fatura
_RAIZ_PROJETO = _MODULO_DIR.parent  # .../itau_agente_rotativo
_CONFIG_PATH = _MODULO_DIR / "config" / "clientes.yml"

# Papéis do schema (chave lógica -> tipo), usados para converter os valores
# crus (sempre string, vindos do CSV) para o tipo esperado pelo resto do
# código. `client_id` fica fora — nunca convertido, é sempre string.
_COLUNAS_FLOAT = {
    "monthly_income",
    "bill_amount",
    "minimum_payment",
    "credit_limit",
    "available_limit",
    "cash_purchases",
    "installment_purchases",
    "interest_installments",
    "previous_balance",
    "fees",
    "previous_bill_amount",
    "previous_payment_amount",
}
_COLUNAS_INT = {"days_to_due", "dias_em_atraso", "consecutive_partial_payments"}
_COLUNAS_BOOL = {
    "previous_revolving",
    "has_active_installment",
    "has_active_renegotiation",
    "eligible_bill_installment",
    "eligible_personal_loan",
}


def _carregar_config(caminho: Path) -> dict:
    """Carrega o YAML de configuração da fonte de clientes."""
    if not caminho.exists():
        raise FileNotFoundError(
            f"[dados_clientes] Arquivo de configuração não encontrado: {caminho}. "
            "Esperado em consultor_fatura/config/clientes.yml."
        )
    with caminho.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolver_caminho_csv(cfg: dict) -> Path:
    origem = cfg.get("source") or {}
    tipo = origem.get("type")
    if tipo != "csv":
        raise ValueError(
            f"[dados_clientes] source.type desconhecido: {tipo!r}. Use 'csv'."
        )

    caminho_relativo = origem.get("path")
    if not caminho_relativo:
        raise ValueError("[dados_clientes] source.path não informado em clientes.yml.")

    # Sempre resolvido a partir da raiz do projeto (via __file__), nunca do
    # cwd — robusto independente de onde o comando for executado.
    caminho = (_RAIZ_PROJETO / caminho_relativo).resolve()
    if not caminho.exists():
        raise FileNotFoundError(
            f"[dados_clientes] Arquivo de dados não encontrado: {caminho} "
            f"(source.path={caminho_relativo!r} em clientes.yml)."
        )
    return caminho


def _validar_colunas(colunas_esperadas: list[str], colunas_disponiveis: list[str], contexto: str) -> None:
    faltantes = [c for c in colunas_esperadas if c not in colunas_disponiveis]
    if faltantes:
        raise ValueError(
            f"[dados_clientes] Coluna(s) mapeada(s) em '{contexto}' não encontrada(s) "
            f"na origem: {faltantes}. Colunas disponíveis: {sorted(colunas_disponiveis)}"
        )


def _converter(papel: str, valor: str) -> Any:
    if papel in _COLUNAS_BOOL:
        return str(valor).strip().lower() in ("true", "1", "sim", "yes")
    if papel in _COLUNAS_INT:
        return int(valor)
    if papel in _COLUNAS_FLOAT:
        return float(valor)
    return valor


def _elegivel_consignado(relationship_type: str) -> bool:
    """Elegibilidade a consignado — deixou de ser dado de origem (não é mais
    coluna do CSV) e virou puro cálculo sobre `relationship_type`.

    MP nº 1.292/2025 ("Crédito do Trabalhador"): CLT do setor privado acessa
    consignado privado diretamente via eSocial, sem depender de convênio —
    por isso a elegibilidade a consignado agora é function só do vínculo,
    mesma regra usada em motor_decisao.calculo.taxa_credito_disponivel.
    """
    return relationship_type in ("servidor_publico", "aposentado_inss", "clt_privado")


def _carregar_clientes() -> dict[str, dict]:
    cfg = _carregar_config(_CONFIG_PATH)
    caminho_csv = _resolver_caminho_csv(cfg)

    colunas_map = cfg.get("columns") or {}
    if "client_id" not in colunas_map:
        raise ValueError("[dados_clientes] columns.client_id não informado em clientes.yml.")

    with caminho_csv.open("r", encoding="utf-8", newline="") as f:
        leitor = csv.DictReader(f)
        colunas_disponiveis = leitor.fieldnames or []
        _validar_colunas(list(colunas_map.values()), colunas_disponiveis, "columns")

        clientes: dict[str, dict] = {}
        coluna_id = colunas_map["client_id"]
        for linha in leitor:
            client_id = linha[coluna_id]
            registro = {
                papel: _converter(papel, linha[coluna_csv])
                for papel, coluna_csv in colunas_map.items()
                if papel != "client_id"
            }
            registro["eligible_payroll_loan"] = _elegivel_consignado(registro["relationship_type"])
            clientes[client_id] = registro

    return clientes


CLIENTES_MOCK = _carregar_clientes()


def avaliar_escopo(cliente: dict) -> tuple[bool, str | None]:
    """Decide se o cliente está dentro do escopo desta jornada (Estágio 1).

    Guardrail de escopo: este motor de cálculo assume Estágio 1 (jornada
    preventiva, fatura em dia ou recém-vencida). Clientes já em atraso
    prolongado ou em renegociação pertencem a outro fluxo, fora deste MVP.

    Regra: fora de escopo se `dias_em_atraso > 30` (caracteriza Estágio
    2/3 — aumento significativo de risco de crédito pela Res. CMN 4.966,
    critério já usado no projeto) OU `has_active_renegotiation=True`.

    Returns:
        (dentro_do_escopo, motivo_fora_escopo) — motivo é None quando
        dentro_do_escopo=True.
    """
    atraso_severo = cliente["dias_em_atraso"] > 30
    em_renegociacao = cliente["has_active_renegotiation"]

    if not atraso_severo and not em_renegociacao:
        return True, None

    if atraso_severo and em_renegociacao:
        motivo = (
            f"Cliente com {cliente['dias_em_atraso']} dias de atraso (Estágio "
            "2/3, Res. CMN 4.966) e já em renegociação ativa — fora do "
            "escopo deste MVP, que assume Estágio 1 (jornada preventiva)."
        )
    elif atraso_severo:
        motivo = (
            f"Cliente com {cliente['dias_em_atraso']} dias de atraso — "
            "mais de 30 dias caracteriza Estágio 2/3 (Res. CMN 4.966), "
            "fora do escopo deste MVP, que assume Estágio 1 (jornada "
            "preventiva)."
        )
    else:
        motivo = (
            "Cliente já está em renegociação ativa — pertence a outro "
            "fluxo, fora do escopo deste MVP (Estágio 1)."
        )

    return False, motivo
