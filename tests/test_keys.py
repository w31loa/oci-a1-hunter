import shutil
import stat
import subprocess

import pytest

from oci_a1_hunter import keys

SNIPPET = """[DEFAULT]
user=ocid1.user.oc1..aaaa
fingerprint=12:34:56:78:9a:bc:de:f0:12:34:56:78:9a:bc:de:f0
tenancy=ocid1.tenancy.oc1..bbbb
region=eu-frankfurt-1
key_file=<path to your private keyfile> # TODO
"""


def test_parse_console_snippet():
    v = keys.parse_console_snippet(SNIPPET)
    assert v["user"] == "ocid1.user.oc1..aaaa"
    assert v["tenancy"] == "ocid1.tenancy.oc1..bbbb"
    assert v["region"] == "eu-frankfurt-1"
    assert v["fingerprint"].startswith("12:34")


def test_api_key_created_once_with_private_permissions(tmp_path):
    path = tmp_path / "k.pem"
    p1, pem, fp = keys.ensure_api_key(path)
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert len(fp.split(":")) == 16
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    _, _, fp2 = keys.ensure_api_key(path)
    assert fp2 == fp  # reused, not regenerated


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl not installed")
def test_fingerprint_matches_openssl(tmp_path):
    path, _, fp = keys.ensure_api_key(tmp_path / "k.pem")
    der = subprocess.run(
        ["openssl", "rsa", "-pubout", "-outform", "DER", "-in", str(path)],
        capture_output=True,
        check=True,
    ).stdout
    import hashlib

    assert fp == ":".join(f"{b:02x}" for b in hashlib.md5(der).digest())


def test_ssh_key(tmp_path):
    pub = keys.ensure_ssh_key(tmp_path / "id_test")
    assert pub.read_text().startswith("ssh-ed25519 ")
    assert stat.S_IMODE((tmp_path / "id_test").stat().st_mode) == 0o600
    if shutil.which("ssh-keygen"):
        out = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(tmp_path / "id_test")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert out.split()[1] == pub.read_text().split()[1]
