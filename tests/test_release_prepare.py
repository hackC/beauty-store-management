# -*- coding: utf-8 -*-
import pathlib, subprocess, sys

def test_prepare_release_never_overwrites_existing_database(tmp_path):
    root=tmp_path/'project'; (root/'data').mkdir(parents=True); (root/'release'/'data').mkdir(parents=True)
    (root/'scripts').mkdir(); (root/'config.example.json').write_text('{"port":8000}',encoding='utf-8')
    (root/'data'/'inventory.db').write_bytes(b'root candidate')
    target=root/'release'/'data'/'inventory.db'; target.write_bytes(b'existing release database')
    script=pathlib.Path(__file__).resolve().parents[1]/'scripts'/'prepare_release.py'
    subprocess.run([sys.executable,'-X','utf8',str(script),'--project-dir',str(root)],check=True,capture_output=True,encoding='utf-8')
    assert target.read_bytes()==b'existing release database'

def test_prepare_release_seeds_only_when_database_missing(tmp_path):
    root=tmp_path/'project'; (root/'data').mkdir(parents=True); (root/'scripts').mkdir()
    (root/'data'/'inventory.db').write_bytes(b'root candidate')
    (root/'config.example.json').write_text('{"port":8000}',encoding='utf-8')
    script=pathlib.Path(__file__).resolve().parents[1]/'scripts'/'prepare_release.py'
    subprocess.run([sys.executable,'-X','utf8',str(script),'--project-dir',str(root)],check=True,capture_output=True,encoding='utf-8')
    assert (root/'release'/'data'/'inventory.db').read_bytes()==b'root candidate'
