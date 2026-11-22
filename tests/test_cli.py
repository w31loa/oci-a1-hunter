"""End-to-end CLI runs against a fake OCI layer (no network)."""

import json
from types import SimpleNamespace

import oci
import pytest
from typer.testing import CliRunner

from oci_a1_hunter import cli
from oci_a1_hunter.cloud import Usage

runner = CliRunner()
CAPACITY = oci.exceptions.ServiceError(500, "InternalError", {}, "Out of host capacity.")


class FakeCloud:
    region = "eu-frankfurt-1"
    tenancy = "ocid1.tenancy.oc1..x"
    launches: list = []
    usage_value = Usage(0, 0, 0, [])
    existing = None

    def __init__(self, cfg):
        self.cfg = cfg
        self.results = [CAPACITY, CAPACITY, SimpleNamespace(id="ocid1.instance.new")]

    def availability_domains(self):
        return ["xxxx:EU-FRANKFURT-1-AD-1"]

    def resolve_image(self):
        return SimpleNamespace(
            id="ocid1.image.x", display_name="Ubuntu-24.04", operating_system="Canonical Ubuntu"
        )

    def find_instance(self, name):
        return self.existing

    def subnets(self):
        return [SimpleNamespace(id="ocid1.subnet.auto", public=True)]

    def get_subnet(self, subnet_id):
        return SimpleNamespace(display_name="public", prohibit_public_ip_on_vnic=False)

    def usage(self, ads):
        return self.usage_value

    def build_launch_details(self, image_id, ssh_key):
        return SimpleNamespace(image_id=image_id, ssh_key=ssh_key)

    def launch(self, details, ad):
        FakeCloud.launches.append(ad)
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def wait_running(self, instance_id):
        return None

    def public_ip(self, instance_id):
        return "203.0.113.17"


@pytest.fixture
def env(tmp_path, monkeypatch):
    FakeCloud.launches = []
    FakeCloud.usage_value = Usage(0, 0, 0, [])
    FakeCloud.existing = None
    monkeypatch.setattr(cli, "Cloud", FakeCloud)
    monkeypatch.setattr(cli, "live_sleep", lambda stats: lambda s: None)
    monkeypatch.setattr(cli, "keep_awake", lambda: None)
    monkeypatch.setattr(oci.util, "to_dict", lambda obj: {"id": obj.id})
    key = tmp_path / "k.pub"
    key.write_text("ssh-ed25519 AAAA test\n")
    out = tmp_path / "instance.json"
    conf = tmp_path / "config.txt"
    conf.write_text(f"ssh_public_key_file = {key}\noutput_file = {out}\ndesktop_notify = no\n")
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(conf=conf, out=out)


def test_hunt_until_success(env):
    result = runner.invoke(cli.app, ["hunt", "-c", str(env.conf)])
    assert result.exit_code == 0, result.output
    assert len(FakeCloud.launches) == 3
    assert "ubuntu@203.0.113.17" in result.output
    data = json.loads(env.out.read_text())
    assert data == {"id": "ocid1.instance.new", "public_ip": "203.0.113.17"}


def test_dry_run_launches_nothing(env):
    result = runner.invoke(cli.app, ["hunt", "-c", str(env.conf), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert FakeCloud.launches == []
    assert "4 CPU / 24 GB" in result.output


def test_refuses_to_exceed_free_tier(env):
    FakeCloud.usage_value = Usage(1, 6, 50, [])
    result = runner.invoke(cli.app, ["hunt", "-c", str(env.conf)])
    assert result.exit_code == 1
    assert "exceeds the Always Free" in result.output
    assert FakeCloud.launches == []


def test_existing_instance_short_circuits(env):
    FakeCloud.existing = SimpleNamespace(id="ocid1.instance.old", lifecycle_state="RUNNING")
    result = runner.invoke(cli.app, ["hunt", "-c", str(env.conf)])
    assert result.exit_code == 0
    assert "already exists" in result.output
    assert FakeCloud.launches == []


def test_flags_override_config(env):
    result = runner.invoke(
        cli.app, ["hunt", "-c", str(env.conf), "--ocpus", "2", "--memory", "12", "--dry-run"]
    )
    assert "2 CPU / 12 GB" in result.output


def test_no_args_runs_hunt_when_config_exists(env):
    result = runner.invoke(cli.app, ["--lang", "en"])
    assert result.exit_code == 0, result.output
    assert len(FakeCloud.launches) == 3


def test_no_args_without_config_offers_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "default_config_path", lambda: tmp_path / "config.txt")
    monkeypatch.setattr(cli, "find_config", lambda _: None)
    monkeypatch.setattr(cli, "open_in_editor", lambda p: None)
    result = runner.invoke(cli.app, ["--lang", "ru"], input="2\n")
    assert result.exit_code == 0, result.output
    text = (tmp_path / "config.txt").read_text()
    assert "Сервер" in text and "ocpus = 4" in text
