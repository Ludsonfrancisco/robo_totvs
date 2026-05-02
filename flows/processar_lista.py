import json
from pathlib import Path

from playwright.sync_api import Page

from core.config import settings
from core.schema import Tecnico, CheckpointItem
from core.estado import carregar_checkpoint, salvar_checkpoint
from core.acoes import baixar_xlsx_tecnico, CredenciaisInvalidasError
from core.log import log

def carregar_tecnicos(incluir_desligados: bool = False) -> list[Tecnico]:
    path = settings.tecnicos_path
    if not path.exists():
        log.bind(etapa="processar_lista").error(f"Arquivo de técnicos não encontrado: {path}")
        raise FileNotFoundError(f"Arquivo {path} não existe")
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    tecnicos = [Tecnico(**item) for item in data]
    
    if not incluir_desligados:
        tecnicos = [t for t in tecnicos if t.status == "Ativo"]
        
    return tecnicos

def processar_lista(page: Page, incluir_desligados: bool = False, retry_falhos: bool = False) -> int:
    tecnicos = carregar_tecnicos(incluir_desligados)
    checkpoint = carregar_checkpoint()
    
    falhas = 0
    processados_agora = 0
    
    log.bind(etapa="processar_lista").info(f"Total de técnicos na lista: {len(tecnicos)}")
    
    for t in tecnicos:
        item = checkpoint.items.get(t.code)
        if not item:
            item = CheckpointItem(code=t.code)
            checkpoint.items[t.code] = item
            salvar_checkpoint(checkpoint)
            
        if item.status == "sucesso":
            log.bind(etapa="processar_lista", tecnico=t.code).info("Técnico já processado com sucesso, pulando.")
            continue
            
        if item.status == "falhou" and not retry_falhos:
            log.bind(etapa="processar_lista", tecnico=t.code).warning("Técnico com falha anterior, pulando (use --retry-falhos).")
            falhas += 1
            continue
            
        item.tentativas += 1
        item.status = "processando"
        salvar_checkpoint(checkpoint)
        
        try:
            resultado = baixar_xlsx_tecnico(page, code=t.code, name=t.name or "")
            item.status = "sucesso"
            item.arquivo = str(resultado["arquivo"])
            item.hash_sha256 = resultado["hash_sha256"]
            item.erro_msg = None
            log.bind(etapa="processar_lista", tecnico=t.code).success(f"Download concluído: {item.arquivo}")
            processados_agora += 1
        except CredenciaisInvalidasError:
            item.status = "falhou"
            item.erro_msg = "Credenciais inválidas"
            salvar_checkpoint(checkpoint)
            raise
        except Exception as e:
            item.status = "falhou"
            item.erro_msg = str(e)
            log.bind(etapa="processar_lista", tecnico=t.code).error(f"Falha ao processar: {e}")
            falhas += 1
            
        salvar_checkpoint(checkpoint)
        
    log.bind(etapa="processar_lista").info(f"Processamento concluído. Processados agora: {processados_agora}, Falhas: {falhas}")
    if falhas > 0:
        return 1
    return 0
