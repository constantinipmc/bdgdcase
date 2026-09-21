# -*- coding: utf-8 -*-
"""Resolução dos diretórios de trabalho.

`caminhos` decide na importação, a partir do ambiente. Por isso cada teste roda
num subprocesso com o ambiente que quer testar — reimportar no processo atual
daria o módulo já resolvido em cache.
"""
import json
import subprocess
import sys


def _rodar(codigo, base=None, env_extra=None):
    env = {k: v for k, v in __import__('os').environ.items()
           if k != 'BDGD_BASE_DIR'}
    if base is not None:
        env['BDGD_BASE_DIR'] = str(base)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, '-c', codigo],
                       capture_output=True, text=True, env=env,
                       cwd=str(base) if base else None)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_variavel_de_ambiente_define_a_base(tmp_path):
    saida = _rodar('from bdgdcase.caminhos import BASE_DIR; print(BASE_DIR)',
                   base=tmp_path)
    assert saida == str(tmp_path.resolve())


def test_diretorios_padrao_penduram_na_base(tmp_path):
    saida = _rodar(
        'from bdgdcase.caminhos import INPUT_DIR, OUTPUT_DIR, OPENDSS_DIR\n'
        'print(INPUT_DIR.name, OUTPUT_DIR.name, OPENDSS_DIR.name)\n'
        'print(OUTPUT_DIR.parent)\n',
        base=tmp_path)
    nomes, pai = saida.splitlines()
    assert nomes == 'input Output OpenDSS'
    assert pai == str(tmp_path.resolve())


def test_json_sobrescreve_e_aceita_caminho_absoluto(tmp_path):
    outro = tmp_path / 'em_outro_disco'
    (tmp_path / 'bdgdcase.json').write_text(
        json.dumps({'output_dir': str(outro), 'opendss_dir': 'casos'}),
        encoding='utf-8')
    saida = _rodar(
        'from bdgdcase.caminhos import OUTPUT_DIR, OPENDSS_DIR\n'
        'print(OUTPUT_DIR); print(OPENDSS_DIR)\n', base=tmp_path)
    absoluto, relativo = saida.splitlines()
    assert absoluto == str(outro)
    assert relativo == str(tmp_path.resolve() / 'casos')


def test_json_quebrado_avisa_mas_nao_impede_o_programa(tmp_path):
    (tmp_path / 'bdgdcase.json').write_text('{ isto não é json', encoding='utf-8')
    saida = _rodar('from bdgdcase.caminhos import OUTPUT_DIR; print(OUTPUT_DIR.name)',
                   base=tmp_path)
    assert saida.splitlines()[-1] == 'Output'


def test_listar_alimentadores_ignora_os_prefixados(tmp_path):
    for nome in ('TRO05', 'IBA09', '_rascunho'):
        (tmp_path / 'Output' / nome).mkdir(parents=True)
    (tmp_path / 'Output' / 'solto.csv').write_text('x', encoding='utf-8')
    saida = _rodar(
        'from bdgdcase.caminhos import listar_alimentadores\n'
        'print(",".join(listar_alimentadores()))\n', base=tmp_path)
    assert saida == 'IBA09,TRO05'


def test_listar_sem_output_da_erro_que_diz_o_caminho(tmp_path):
    saida = _rodar(
        'from bdgdcase.caminhos import listar_alimentadores\n'
        'try:\n'
        '    listar_alimentadores()\n'
        'except FileNotFoundError as e:\n'
        '    print("Output" in str(e))\n', base=tmp_path)
    assert saida == 'True'


def test_resumo_mostra_o_estado_de_cada_caminho(tmp_path):
    (tmp_path / 'Output').mkdir()
    saida = _rodar('from bdgdcase.caminhos import resumo; print(resumo())',
                   base=tmp_path)
    assert 'BDGD_BASE_DIR' in saida
    assert 'Output/' in saida and 'OK' in saida
    assert 'NAO ENCONTRADO' in saida       # input/ e OpenDSS/ não existem
