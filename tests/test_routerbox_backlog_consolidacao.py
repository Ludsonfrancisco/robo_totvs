from pathlib import Path

import pandas as pd


def _criar_xlsx_routerbox(path: Path, linhas: list[dict]) -> Path:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{"Campo": "fixture", "Valor": path.name}]).to_excel(
            writer, sheet_name="Resumo", index=False
        )
        pd.DataFrame(linhas).to_excel(
            writer, sheet_name="Relatório de Atendimentos", index=False
        )
    return path


def test_consolidar_normaliza_fluxo_e_preserva_original(tmp_path):
    from flows.routerbox_backlog import consolidar_backlogs

    acerta = _criar_xlsx_routerbox(
        tmp_path / "acerta_backlog.xlsx",
        [
            {
                "Numero": "1001",
                "Cliente": "Cliente Acerta",
                "Fluxo": "#1.43 VAR DESCONECTA GERAL -ME",
                "Data AB": "09/06/2026",
                "Hora AB": "15:00:00",
                "Tel. Cel.": "27999990000",
            }
        ],
    )
    loga = _criar_xlsx_routerbox(
        tmp_path / "loga_backlog.xlsx",
        [
            {
                "Numero": "2001",
                "Cliente": "Cliente Loga",
                "Fluxo": "#1.25 INSTALACAO",
                "Data AB": "09/06/2026",
                "Hora AB": "15:43:59",
                "Tel. Cel.": "28999990000",
            }
        ],
    )
    out = tmp_path / "BACKLOG-GERAL-CONSOLIDADO.xlsx"

    resumo = consolidar_backlogs(acerta_path=acerta, loga_path=loga, output_path=out)

    df = pd.read_excel(out, sheet_name="Relatório de Atendimentos", dtype=str)
    assert out.exists()
    assert len(df) == 2
    assert "Fluxo Original" in df.columns
    assert "Origem RouterBox" in df.columns
    assert df["Fluxo"].str.len().max() <= 10
    assert set(df["Fluxo"]) == {"1.43", "1.25"}
    assert "#1.43 VAR DESCONECTA GERAL -ME" in set(df["Fluxo Original"])
    assert set(df["Origem RouterBox"]) == {"ACERTA", "LOGA"}
    assert resumo["linhas_total"] == 2
    assert resumo["linhas_acerta"] == 1
    assert resumo["linhas_loga"] == 1
    assert resumo["ultima_data_ab"] == "2026-06-09 15:43:59"


def test_consolidar_rejeita_xlsx_invalido(tmp_path):
    from flows.routerbox_backlog import consolidar_backlogs

    invalido = tmp_path / "acerta_backlog.xlsx"
    invalido.write_text("nao sou xlsx", encoding="utf-8")
    loga = _criar_xlsx_routerbox(
        tmp_path / "loga_backlog.xlsx",
        [
            {
                "Numero": "2001",
                "Cliente": "Cliente Loga",
                "Fluxo": "#1.25 INSTALACAO",
                "Data AB": "09/06/2026",
                "Hora AB": "15:43:59",
                "Tel. Cel.": "28999990000",
            }
        ],
    )

    try:
        consolidar_backlogs(
            acerta_path=invalido,
            loga_path=loga,
            output_path=tmp_path / "out.xlsx",
        )
    except ValueError as exc:
        assert "XLSX inválido" in str(exc)
    else:
        raise AssertionError("consolidar_backlogs deveria rejeitar XLSX inválido")
