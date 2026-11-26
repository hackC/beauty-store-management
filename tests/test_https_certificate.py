# -*- coding: utf-8 -*-
import json, pathlib, subprocess, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from application_test_support import unload_test_application

def test_generate_lan_certificate_updates_release_config(tmp_path):
    root=pathlib.Path(__file__).resolve().parents[1]
    out=tmp_path/'certs';config=tmp_path/'config.json'
    config.write_text(json.dumps({'port':8000,'https':False}),encoding='utf-8')
    script=root/'scripts'/'generate_lan_certificate.py'
    subprocess.run([sys.executable,'-X','utf8',str(script),'--ip','192.168.1.20','--domain','ku.home.arpa','--output-dir',str(out),'--config',str(config)],check=True,capture_output=True,encoding='utf-8')
    saved=json.loads(config.read_text(encoding='utf-8'))
    assert saved['https'] is True
    assert saved['ssl_certfile']=='data/certs/server.crt'
    ca=x509.load_pem_x509_certificate((out/'ca.crt').read_bytes())
    server=x509.load_pem_x509_certificate((out/'server.crt').read_bytes())
    assert server.issuer==ca.subject
    assert server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)[0].compressed=='192.168.1.20'
    assert (out/'server.key').is_file() and (out/'ca.key').is_file()

def test_server_auto_certificate_generation_is_non_overwriting(tmp_path, monkeypatch):
    import importlib
    import os
    import sys
    root=pathlib.Path(__file__).resolve().parents[1]
    old=os.getcwd();os.chdir(tmp_path);sys.path.insert(0,str(root))
    try:
        sys.modules.pop('main',None)
        main=importlib.import_module('main')
        monkeypatch.setattr(main,'DATA_DIR',tmp_path/'data')
        monkeypatch.setattr(main,'get_lan_ip',lambda:'192.168.5.20')
        cert,key=main.ensure_self_signed_certificate('salon.home.arpa')
        cert_path=pathlib.Path(cert);key_path=pathlib.Path(key)
        assert cert_path.is_file() and key_path.is_file()
        ca=x509.load_pem_x509_certificate((tmp_path/'data'/'certs'/'ca.crt').read_bytes())
        server=x509.load_pem_x509_certificate(cert_path.read_bytes())
        assert server.issuer==ca.subject
        san=server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert 'salon.home.arpa' in san.get_values_for_type(x509.DNSName)
        assert '192.168.5.20' in [x.compressed for x in san.get_values_for_type(x509.IPAddress)]
        cert_path.write_text('manual-certificate-marker',encoding='utf-8')
        cert2,key2=main.ensure_self_signed_certificate('salon.home.arpa')
        assert pathlib.Path(cert2).read_text(encoding='utf-8')=='manual-certificate-marker'
        partial_dir=tmp_path/'partial'/'certs';partial_dir.mkdir(parents=True)
        (partial_dir/'server.crt').write_text('manual-partial',encoding='utf-8')
        monkeypatch.setattr(main,'DATA_DIR',tmp_path/'partial')
        import pytest
        with pytest.raises(RuntimeError,match='不完整'):
            main.ensure_self_signed_certificate('salon.home.arpa')
    finally:
        os.chdir(old);sys.path.remove(str(root));unload_test_application()
