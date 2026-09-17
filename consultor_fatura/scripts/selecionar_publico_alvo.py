#!/usr/bin/env python3
"""Seleciona o público-alvo de teste/demo a partir da base de clientes.

Filtra os client_ids que atendem aos critérios da jornada (elegíveis para
a demonstração ao vivo):

    - dentro_do_escopo=True (ver avaliar_escopo em
      consultor_fatura/dados_clientes.py — Estágio 1, sem atraso >30 dias
      e sem renegociação ativa)
    - fatura próxima do vencimento (days_to_due dentro de uma janela,
      padrão 0 a 5 dias)
    - sem has_active_renegotiation (redundante com dentro_do_escopo hoje,
      mas mantido como filtro explícito para o dia continuar correto
      mesmo se a regra de escopo mudar)

Por enquanto lê de `CLIENTES_MOCK` (consultor_fatura/dados_clientes.py).
No dia do evento, trocar só `_carregar_base()` por uma leitura real
(planilha/consulta ao core bancário) — o resto do script (filtragem, CLI,
saída) não muda, desde que a função continue devolvendo um dict
{client_id: {...schema...}} no mesmo formato de CLIENTES_MOCK.

Uso:
    python selecionar_publico_alvo.py
    python selecionar_publico_alvo.py --dias-min 0 --dias-max 7
    python selecionar_publico_alvo.py --config criterios.json
    python selecionar_publico_alvo.py --json

Saída (stdout): um client_id por linha (ou um array JSON com --json) —
pronta para escolher quem testar: abra uma conversa nova com o agente e,
quando ele perguntar o código de cliente, informe um dos client_ids desta
lista (ver "Como testar" em consultor_fatura/README.md).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# Importa dados_clientes.py direto pelo caminho do arquivo, SEM passar por
# consultor_fatura/__init__.py — esse __init__ faz `from . import agent`,
# que puxa o google-adk (exigido pelo `adk web`, mas não por este script).
# Este script só precisa da base de clientes e da regra de escopo, então
# roda em qualquer Python, sem precisar do ambiente com o ADK instalado.
_DADOS_CLIENTES_PATH = Path(__file__).resolve().parents[1] / "dados_clientes.py"
_spec = importlib.util.spec_from_file_location("dados_clientes", _DADOS_CLIENTES_PATH)
_dados_clientes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dados_clientes)
CLIENTES_MOCK = _dados_clientes.CLIENTES_MOCK
avaliar_escopo = _dados_clientes.avaliar_escopo

_CRITERIOS_PADRAO = {
    "dias_min": 0,
    "dias_max": 5,
}


def _carregar_base() -> dict[str, dict[str, Any]]:
    """Fonte dos clientes a filtrar.

    Hoje: o mock de desenvolvimento. No dia do evento, trocar por uma
    leitura real (planilha/consulta) que devolva o mesmo formato
    {client_id: {...schema de dados_clientes.CLIENTES_MOCK...}} — o resto
    do script não precisa mudar.
    """
    return CLIENTES_MOCK


def _carregar_criterios(args: argparse.Namespace) -> dict[str, int]:
    """Resolve os critérios de filtragem: config (se houver) < flags de CLI."""
    criterios = dict(_CRITERIOS_PADRAO)

    if args.config:
        with open(args.config, encoding="utf-8") as f:
            criterios.update(json.load(f))

    if args.dias_min is not None:
        criterios["dias_min"] = args.dias_min
    if args.dias_max is not None:
        criterios["dias_max"] = args.dias_max

    return criterios


def selecionar_publico_alvo(
    base: dict[str, dict[str, Any]], criterios: dict[str, int]
) -> list[str]:
    """Retorna os client_ids elegíveis para a demonstração, na ordem da base.

    Critérios: dentro_do_escopo=True, days_to_due entre criterios["dias_min"]
    e criterios["dias_max"] (inclusive), e sem renegociação ativa.
    """
    dias_min = criterios["dias_min"]
    dias_max = criterios["dias_max"]

    selecionados = []
    for client_id, cliente in base.items():
        dentro_do_escopo, _ = avaliar_escopo(cliente)
        if not dentro_do_escopo:
            continue
        if cliente["has_active_renegotiation"]:
            continue
        if not (dias_min <= cliente["days_to_due"] <= dias_max):
            continue
        selecionados.append(client_id)

    return selecionados


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dias-min", type=int, default=None, help="days_to_due mínimo (padrão: 0)"
    )
    parser.add_argument(
        "--dias-max", type=int, default=None, help="days_to_due máximo (padrão: 5)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Arquivo JSON com critérios (ex.: {\"dias_min\": 0, \"dias_max\": 7})",
    )
    parser.add_argument(
        "--json", action="store_true", help="Saída como array JSON em vez de 1 client_id por linha"
    )
    args = parser.parse_args()

    criterios = _carregar_criterios(args)
    base = _carregar_base()
    selecionados = selecionar_publico_alvo(base, criterios)

    if args.json:
        print(json.dumps(selecionados, ensure_ascii=False, indent=2))
    else:
        for client_id in selecionados:
            print(client_id)

    print(f"{len(selecionados)} cliente(s) selecionado(s).", file=sys.stderr)


if __name__ == "__main__":
    main()
