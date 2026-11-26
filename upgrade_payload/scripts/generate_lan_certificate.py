# -*- coding: utf-8 -*-
"""生成门店局域网 HTTPS 私有 CA/服务器证书，便于手机启用摄像头扫码。"""
import argparse
import ipaddress
import json
import pathlib
import socket
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def detect_lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="生成本地 HTTPS 证书并更新服务端配置")
    parser.add_argument("--ip", default=detect_lan_ip(), help="服务器局域网 IPv4 地址")
    parser.add_argument("--domain", default="ku.home.arpa", help="局域网 DNS 名称")
    parser.add_argument("--output-dir", default="release/data/certs", help="证书输出目录")
    parser.add_argument("--config", default="release/data/config.json", help="要启用 HTTPS 的 config.json")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]
    output = pathlib.Path(args.output_dir)
    if not output.is_absolute():
        output = root / output
    config_path = pathlib.Path(args.config)
    if not config_path.is_absolute():
        config_path = root / config_path
    output.mkdir(parents=True, exist_ok=True)

    ip_address = ipaddress.ip_address(args.ip)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Store Inventory Local LAN CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, args.domain or str(ip_address))])
    san = [x509.IPAddress(ip_address)]
    if args.domain:
        san.append(x509.DNSName(args.domain))
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    (output / "ca.key").write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (output / "ca.crt").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    (output / "server.key").write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (output / "server.crt").write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))

    if not config_path.exists():
        raise SystemExit(f"配置文件不存在：{config_path}；请先编译/准备 release 目录")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["https"] = True
    config["ssl_certfile"] = "data/certs/server.crt"
    config["ssl_keyfile"] = "data/certs/server.key"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"证书已生成：{output}")
    print(f"服务器地址：https://{args.domain}:{config.get('port', 8000)} 或 https://{args.ip}:{config.get('port', 8000)}")
    print(f"请将 {output / 'ca.crt'} 安装并信任到每台手机/电脑；不要分发 ca.key 或 server.key。")
    print("已将 HTTPS 写入配置；请重启服务端。")


if __name__ == "__main__":
    main()
