"""Visão computacional — template matching sobre screenshots do Playwright.

Pipeline alinhado ao PRD §8.1:
1. Captura screenshot da viewport via Playwright.
2. Carrega referência de `referencias/`.
3. Detecta seta vermelha de marcação (caso exista) — sua ponta vira o
   ponto de clique e ela é mascarada (inpaint) antes do matching.
4. Multi-scale template matching com `cv2.TM_CCOEFF_NORMED`.
5. Acima do threshold → ponto de clique escalado; abaixo → None.

Notas:
- Threshold default 0.70 (mais permissivo que o 0.85 do PRD pois algumas
  referências contêm conteúdo pré-preenchido — usuário/senha digitados).
- Multi-scale inclui auto-fit quando o template é maior que a viewport
  (caso de `06_clicar_entrar.png`, `07_pagina_home_*.png`, `18_*.png`).
- A seta vermelha é convenção do projeto: indica o alvo do clique. Se a
  referência não tiver seta, o clique cai no centro geométrico do match.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
from playwright.sync_api import Page

from core.config import settings
from core.log import log

THRESHOLD_DEFAULT = 0.65
ESCALAS = (0.9, 1.0, 1.1)
INTERVALO_POLLING_S = 0.5
LIMITE_FALHAS_CONTEXTO = 3

_falhas_consecutivas = 0


def _resolver_caminho(referencia: str) -> Path:
    """Aceita nome relativo ou absoluto. Tolera arquivo sem extensão."""
    p = Path(referencia)
    if not p.is_absolute():
        p = settings.referencias_dir / p
    if not p.exists() and p.suffix == "":
        cand = p.with_suffix(".png")
        if cand.exists():
            p = cand
    if not p.exists():
        raise FileNotFoundError(f"referência não encontrada: {p}")
    return p


def _decode_screenshot(buf: bytes) -> np.ndarray:
    arr = np.frombuffer(buf, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _mascara_seta_vermelha(template: np.ndarray) -> np.ndarray | None:
    """Máscara binária dos pixels da seta vermelha (R alto, G/B baixos).

    Retorna None se não houver área vermelha relevante (< 30 px).
    """
    b, g, r = cv2.split(template)
    r_i, g_i, b_i = r.astype(np.int16), g.astype(np.int16), b.astype(np.int16)
    cond = (r_i > 150) & ((r_i - g_i) > 60) & ((r_i - b_i) > 60)
    mask = cond.astype(np.uint8) * 255
    if cv2.countNonZero(mask) < 30:
        return None
    # dilata levemente para cobrir contornos suavizados da seta
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.dilate(mask, kernel, iterations=1)


def _ponta_da_seta(mask: np.ndarray, template: np.ndarray) -> tuple[int, int]:
    """Heurística: ponta = pixel vermelho com maior densidade local.

    A cabeça (ponta) de uma seta desenhada é tipicamente preenchida/triangular,
    enquanto a cauda é uma linha fina. Aplicar um box filter na máscara binária
    realça a região mais densa — onde está a cabeça da seta.
    """
    bin_f = (mask > 0).astype(np.float32)
    densidade = cv2.boxFilter(bin_f, -1, (21, 21))
    densidade_mascarada = densidade * bin_f
    y, x = np.unravel_index(int(np.argmax(densidade_mascarada)), densidade.shape)
    return int(x), int(y)


def _preparar_template(template: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """Retorna (template_para_matching, (offset_x, offset_y)).

    - Se houver seta vermelha: aplica inpaint para removê-la e usa a ponta
      como offset de clique relativo ao top-left.
    - Caso contrário: retorna template original e offset = centro.
    """
    h, w = template.shape[:2]
    mask = _mascara_seta_vermelha(template)
    if mask is None:
        return template, (w // 2, h // 2)
    ponta = _ponta_da_seta(mask, template)
    limpo = cv2.inpaint(template, mask, 3, cv2.INPAINT_TELEA)
    return limpo, ponta


def _localizar(
    screenshot: np.ndarray,
    template_match: np.ndarray,
    offset_template: tuple[int, int],
    threshold: float,
) -> tuple[float, tuple[int, int]] | None:
    sh, sw = screenshot.shape[:2]
    th, tw = template_match.shape[:2]
    off_x, off_y = offset_template

    # Auto-fit: se template > viewport, base_scale encolhe pra caber com folga.
    fit = min(sw / tw, sh / th, 1.0)
    base = fit * 0.95 if fit < 1.0 else 1.0

    melhor_val = 0.0
    melhor_pos: tuple[int, int] | None = None
    for fator in ESCALAS:
        escala = base * fator
        nh, nw = int(th * escala), int(tw * escala)
        if nh < 10 or nw < 10 or nh > sh or nw > sw:
            continue
        interp = cv2.INTER_AREA if escala < 1.0 else cv2.INTER_CUBIC
        resized = cv2.resize(template_match, (nw, nh), interpolation=interp)
        result = cv2.matchTemplate(screenshot, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > melhor_val:
            melhor_val = max_val
            x = max_loc[0] + int(off_x * escala)
            y = max_loc[1] + int(off_y * escala)
            melhor_pos = (x, y)

    if melhor_pos is not None and melhor_val >= threshold:
        return melhor_val, melhor_pos
    return None


def aguardar_imagem(
    page: Page,
    referencia: str,
    timeout: int = 15,
    threshold: float = THRESHOLD_DEFAULT,
) -> tuple[int, int] | None:
    """Faz polling até localizar a referência no screenshot atual.

    Retorna (x, y) do centro do match, ou None em timeout.
    """
    global _falhas_consecutivas
    template_path = _resolver_caminho(referencia)
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise RuntimeError(f"falha ao decodificar referência: {template_path}")
    template_match, offset = _preparar_template(template)

    deadline = time.monotonic() + timeout
    tentativa = 0
    melhor_score = 0.0
    while time.monotonic() < deadline:
        tentativa += 1
        screenshot = _decode_screenshot(page.screenshot(full_page=False))
        match = _localizar(screenshot, template_match, offset, threshold)
        if match is not None:
            score, ponto = match
            log.bind(etapa="visao").debug(
                f"match {referencia} score={score:.3f} ponto={ponto} (tentativa {tentativa})"
            )
            _falhas_consecutivas = 0
            return ponto
        diagn = _localizar(screenshot, template_match, offset, threshold=0.0)
        if diagn is not None:
            melhor_score = max(melhor_score, diagn[0])
        time.sleep(INTERVALO_POLLING_S)

    log.bind(etapa="visao").warning(
        f"timeout {timeout}s aguardando {referencia} "
        f"(melhor score visto: {melhor_score:.3f}, threshold {threshold:.2f})"
    )

    _falhas_consecutivas += 1
    if _falhas_consecutivas >= LIMITE_FALHAS_CONTEXTO:
        try:
            from core.contexto import classificar_tela
            settings.logs_dir.joinpath("evidencias").mkdir(parents=True, exist_ok=True)
            screenshot_path = str(settings.logs_dir / "evidencias" / "contexto_trigger.png")
            with open(screenshot_path, "wb") as f:
                f.write(page.screenshot(full_page=False))
            classificar_tela(screenshot_path)
            _falhas_consecutivas = 0
        except Exception as e:
            log.bind(etapa="visao").error(f"Erro ao invocar classificador de contexto: {e}")

    return None


def clicar_imagem(
    page: Page,
    referencia: str,
    timeout: int = 15,
    threshold: float = THRESHOLD_DEFAULT,
) -> bool:
    """Aguarda a referência e clica no centro. Retorna True se clicou."""
    centro = aguardar_imagem(page, referencia, timeout=timeout, threshold=threshold)
    if centro is None:
        return False
    x, y = centro
    page.mouse.click(x, y)
    log.bind(etapa="visao").info(f"clicou em {referencia} ({x}, {y})")
    return True

def validar_texto_ocr(page: Page, texto_esperado: str) -> bool:
    """Extrai texto da tela atual via OCR e compara fuzzy com texto_esperado.
    Retorna True se encontrar correspondência alta, False caso contrário.
    """
    try:
        import pytesseract
        from rapidfuzz import fuzz, process
    except ImportError:
        log.bind(etapa="ocr").warning("pytesseract ou rapidfuzz não instalados. Pulando OCR.")
        return True

    try:
        screenshot = _decode_screenshot(page.screenshot(full_page=False))
        # Pré-processamento simples para melhorar OCR
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        
        texto_extraido = pytesseract.image_to_string(thresh, lang='por')
        linhas = [linha.strip() for linha in texto_extraido.split('\n') if linha.strip()]
        
        if not linhas:
            return False
            
        # Usa fuzzy partial ratio para ver se o nome do técnico está em alguma linha
        # Um score >= 80 é aceitável para correspondência parcial de nomes
        melhor_match = process.extractOne(texto_esperado, linhas, scorer=fuzz.partial_ratio)
        
        if melhor_match and melhor_match[1] >= 80:
            log.bind(etapa="ocr").info(f"Match OCR: '{texto_esperado}' ~ '{melhor_match[0]}' (score {melhor_match[1]:.1f})")
            return True
        else:
            melhor_str = melhor_match[0] if melhor_match else "N/A"
            score_val = melhor_match[1] if melhor_match else 0.0
            log.bind(etapa="ocr").warning(
                f"OCR mismatch. Esperado: '{texto_esperado}'. Melhor na tela: '{melhor_str}' (score {score_val:.1f})"
            )
            return False
    except Exception as e:
        log.bind(etapa="ocr").error(f"Erro ao executar OCR: {e}")
        return False
