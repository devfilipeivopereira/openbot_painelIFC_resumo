#!/usr/bin/env python3
"""Turn technical source states in the panel 'Atenção agora' cards into plain wording (04/11).

Purely technical pendings (format/curation/processing) are dropped from the decision cards;
they stay untouched in data['fontes'] (Status das fontes, collapsed in the panel).
Idempotent; preserves mtime/owner/mode so health checks on publication time are unaffected."""
import json, os, re, tempfile
from pathlib import Path

PANEL = Path('/opt/ifc-painel/published/dados.json')
NAMES = {
    'drive_secretaria': 'Drive da secretaria', 'email_secretaria': 'E-mail da secretaria',
    'grupos': 'Grupos de WhatsApp', 'mulheres': 'Grupo Mulheres IFC', 'mensagens_diretas': 'Mensagens diretas',
    'instagram': 'Instagram', 'youtube': 'YouTube', 'agenda': 'Agenda', 'todoist': 'Todoist',
    'financeiro': 'Financeiro', 'visitantes': 'Visitantes', 'batismos_planilha': 'Planilha de batismos',
    'livro_oracao': 'Livro de Oração', 'curadoria_detalhada': 'Análise das conversas', 'n8n': 'Automações (n8n)',
}

def friendly(source):
    return NAMES.get(source) or source.replace('_', ' ').strip().capitalize() or 'Uma fonte'

def humanize(item):
    """Return a human card, or None to drop a purely technical pending."""
    area = str(item.get('area', ''))
    if not area.startswith('Fonte · '):
        return item
    source = area[len('Fonte · '):].strip()
    state = str(item.get('titulo', '')).strip()
    name = friendly(source)
    low = state.casefold()
    if low.startswith('falhou'):
        return {'area': 'Fontes do painel', 'titulo': f'{name}: a atualização de hoje falhou',
                'detalhe': 'O painel está mostrando os últimos dados salvos dessa fonte. Detalhes técnicos em Status das fontes.',
                'fonte': source}
    if low.startswith('atenção: cópias divergentes'):
        return {'area': 'Fontes do painel', 'titulo': f'{name}: as cópias estão diferentes',
                'detalhe': 'Vale conferir com a secretaria qual versão está certa.', 'fonte': source}
    if low.startswith(('atenção: última captura preservada', 'atenção: cobertura incompleta', 'atenção: conteúdo parcial', 'não verificada')):
        return {'area': 'Fontes do painel', 'titulo': f'{name}: dados de hoje incompletos',
                'detalhe': 'Parte do conteúdo não pôde ser lida nesta atualização; o resumo usa o que estava disponível.', 'fonte': source}
    if low.startswith('pendente') or 'curadoria' in low or source == 'curadoria_detalhada':
        return None  # technical processing note: lives only in Status das fontes
    return {'area': 'Fontes do painel', 'titulo': f'{name}: precisa de conferência',
            'detalhe': 'Detalhes técnicos em Status das fontes.', 'fonte': source}

def main():
    try:
        st = PANEL.stat(); data = json.loads(PANEL.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    items = data.get('atencao')
    if not isinstance(items, list):
        return 0
    new = [h for h in (humanize(i) if isinstance(i, dict) else i for i in items) if h is not None]
    if new == items:
        return 0
    data['atencao'] = new
    fd, tmp = tempfile.mkstemp(prefix='.painel-human-', suffix='.tmp', dir=PANEL.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False, separators=(',', ':')); out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.chown(tmp, st.st_uid, st.st_gid); os.chmod(tmp, st.st_mode & 0o777)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, PANEL)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
