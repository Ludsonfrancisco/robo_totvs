"""Diagnóstico — inspeciona o DOM da tela de login após o OK do Programa Inicial.

Uso:
    venv/bin/python scripts/inspect_login_dom.py
"""

import time

from core.config import settings
from core.log import log
from core.navegador import iniciar_navegador, tirar_screenshot


def main() -> int:
    with iniciar_navegador() as (_, _, page):
        page.goto(settings.PROTHEUS_URL, wait_until="domcontentloaded")
        time.sleep(3)

        # Click OK Programa Inicial via DOM (sabemos que funciona).
        try:
            page.get_by_role("button", name="OK").first.click(timeout=10_000)
            print("[OK] click no OK do Programa Inicial")
        except Exception as e:
            print(f"[ERRO] OK falhou: {e}")
            return 1

        # Espera 8s para a tela de login aparecer.
        time.sleep(8)
        tirar_screenshot(page, etapa="diag_pos_ok")

        print("\n=== FRAMES ===")
        for i, f in enumerate(page.frames):
            print(f"[{i}] url={f.url!r}  name={f.name!r}")

        print("\n=== INPUTS NO MAIN FRAME ===")
        inputs = page.evaluate(
            """() => Array.from(document.querySelectorAll('input')).map(i => ({
                type: i.type,
                placeholder: i.placeholder,
                name: i.name,
                id: i.id,
                visible: i.offsetParent !== null,
                rect: i.getBoundingClientRect().toJSON(),
            }))"""
        )
        for inp in inputs:
            print(inp)

        print("\n=== INPUTS POR FRAME ===")
        for i, f in enumerate(page.frames):
            if f == page.main_frame:
                continue
            try:
                items = f.evaluate(
                    """() => Array.from(document.querySelectorAll('input')).map(i => ({
                        type: i.type, placeholder: i.placeholder,
                        name: i.name, id: i.id,
                        visible: i.offsetParent !== null,
                    }))"""
                )
                if items:
                    print(f"-- frame [{i}] {f.url}")
                    for it in items:
                        print(" ", it)
            except Exception as e:
                print(f"-- frame [{i}] erro: {e}")

        print("\n=== BUTTONS NO MAIN FRAME ===")
        buttons = page.evaluate(
            """() => Array.from(document.querySelectorAll('button')).map(b => ({
                text: b.textContent?.trim(),
                disabled: b.disabled,
                visible: b.offsetParent !== null,
            }))"""
        )
        for b in buttons:
            print(b)

        print("\n=== TIPOS DE TAGS NO MAIN FRAME ===")
        # Conta tags
        counts = page.evaluate(
            """() => {
                const c = {};
                document.querySelectorAll('*').forEach(e => {
                    c[e.tagName] = (c[e.tagName] || 0) + 1;
                });
                return c;
            }"""
        )
        print(counts)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
