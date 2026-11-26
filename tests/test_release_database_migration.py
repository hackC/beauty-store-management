# -*- coding: utf-8 -*-
import os, pathlib, shutil, sys, tempfile, sqlite3, importlib
from legacy_database_fixture import write_legacy_database

from application_test_support import unload_test_application

def test_release_database_incremental_migration_preserves_business_data_without_recreating_users():
    source=pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='release_db_migration_') as td:
        project=pathlib.Path(td)/'project'
        shutil.copytree(source,project,ignore=shutil.ignore_patterns('__pycache__','*.pyc','build','dist','release','logs'))
        target=project/'release'/'data'/'inventory.db';target.parent.mkdir(parents=True)
        write_legacy_database(target, 84)
        old=os.getcwd();os.chdir(project);sys.path.insert(0,str(project))
        try:
            sys.modules.pop('main',None);main=importlib.import_module('main');main.DB_PATH=target;main.init_db()
            con=sqlite3.connect(f'file:{target}?mode=ro',uri=True)
            try:
                assert con.execute('select count(*) from products').fetchone()[0]==84
                assert con.execute('select count(*) from users').fetchone()[0]==0
                assert con.execute('select count(*) from user_sessions').fetchone()[0]==0
                assert 'barcode' in {r[1] for r in con.execute('pragma table_info(products)')}
                assert con.execute("select count(*) from sqlite_master where type='table' and name='schedule_state'").fetchone()[0]==1
                assert con.execute('pragma integrity_check').fetchone()[0]=='ok'
            finally: con.close()
        finally:
            os.chdir(old);sys.path.remove(str(project));unload_test_application()
