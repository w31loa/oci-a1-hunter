"""Drive the whole setup wizard with scripted answers against a fake OCI layer."""

from types import SimpleNamespace

from typer.testing import CliRunner

from oci_a1_hunter import cli, wizard
from oci_a1_hunter.cloud import SubnetInfo, Usage
from oci_a1_hunter.config import Config, load_config

FP = "12:34:56:78:9a:bc:de:f0:12:34:56:78:9a:bc:de:f0"


class FakeCloud:
    created_network = False

    def __init__(self, cfg):
        self.cfg = cfg
        self.region = cfg.region or "eu-frankfurt-1"
        self.tenancy = cfg.tenancy

    def availability_domains(self):
        return ["xxxx:EU-FRANKFURT-1-AD-1"]

    def regions(self):
        return [("eu-frankfurt-1", True)]

    def usage(self, ads):
        return Usage(0, 0, 0, [])

    def images(self):
        img = lambda os_, v: SimpleNamespace(  # noqa: E731
            operating_system=os_, operating_system_version=v, display_name=f"{os_}-{v}"
        )
        return [
            img("Oracle Linux", "9"),
            img("Canonical Ubuntu", "22.04"),
            img("Canonical Ubuntu", "24.04"),
        ]

    def subnets(self):
        return []

    def create_public_network(self):
        FakeCloud.created_network = True
        return SubnetInfo(
            "ocid1.subnet.new", "a1-hunter-public", "a1-hunter-vcn", "10.0.0.0/24", True
        )


def test_wizard_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard, "Cloud", FakeCloud)
    monkeypatch.setattr(wizard, "Config", lambda: Config(oci_config_file=str(tmp_path / "none")))
    key = tmp_path / "fake_api_key"
    monkeypatch.setattr(wizard.keys, "ensure_api_key", lambda: (key, "-----PUB-----", FP))
    monkeypatch.setattr(wizard.keys, "default_ssh_public_key", lambda: None)
    monkeypatch.setattr(wizard.keys, "ensure_ssh_key", lambda: tmp_path / "ssh.pub")

    answers = [
        "1",  # create the API key for me
        "[DEFAULT]",
        "user=ocid1.user.oc1..u",
        f"fingerprint={FP}",
        "tenancy=ocid1.tenancy.oc1..t",
        "region=eu-frankfurt-1",  # all four keys present -> stops reading
        "2",  # size preset: 2 CPU / 12 GB
        "100",  # disk
        "my-vm",  # name
        "",  # OS: default (Ubuntu 24.04)
        "y",  # create network
        "1",  # notifications: desktop only
        "n",  # don't start hunting
    ]
    out = tmp_path / "config.txt"
    result = CliRunner().invoke(cli.app, ["init", "-c", str(out)], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output

    cfg = load_config(out)
    assert cfg.tenancy == "ocid1.tenancy.oc1..t" and cfg.user == "ocid1.user.oc1..u"
    assert cfg.fingerprint == FP and cfg.key_file == str(key) and cfg.region == "eu-frankfurt-1"
    assert (cfg.ocpus, cfg.memory_gb, cfg.boot_volume_gb) == (2, 12, 100)
    assert cfg.display_name == "my-vm"
    assert cfg.image == "Canonical Ubuntu 24.04"
    assert cfg.subnet_id == "ocid1.subnet.new" and FakeCloud.created_network
    assert cfg.ssh_public_key_file == str(tmp_path / "ssh.pub")
    assert oct(out.stat().st_mode & 0o777) == "0o600"
