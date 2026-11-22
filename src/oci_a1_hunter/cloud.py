"""Thin wrapper over the OCI Python SDK: one set of clients reused for every attempt."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import oci
from oci.pagination import list_call_get_all_results

from oci_a1_hunter.config import Config

GONE_STATES = {"TERMINATED", "TERMINATING"}
NO_RETRY = oci.retry.NoneRetryStrategy()
TIMEOUT = (10, 90)  # connect, read


@dataclass
class Usage:
    a1_ocpus: float
    a1_memory_gb: float
    storage_gb: int
    instances: list[Any]


@dataclass
class SubnetInfo:
    id: str
    name: str
    vcn_name: str
    cidr: str
    public: bool


def build_oci_config(cfg: Config) -> dict[str, Any]:
    """OCI SDK config from the inline fields in config.txt, or from ~/.oci/config."""
    if cfg.has_inline_auth:
        conf: dict[str, Any] = {
            "tenancy": cfg.tenancy,
            "user": cfg.user,
            "fingerprint": cfg.fingerprint,
            "key_file": os.path.expanduser(cfg.key_file),
            "region": cfg.region,
        }
    else:
        conf = oci.config.from_file(os.path.expanduser(cfg.oci_config_file), cfg.oci_profile)
        if cfg.region:
            conf["region"] = cfg.region
    oci.config.validate_config(conf)
    return conf


class Cloud:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.oci_config = build_oci_config(cfg)
        self.tenancy: str = self.oci_config["tenancy"]
        self.region: str = self.oci_config.get("region", "")
        self.compartment: str = cfg.compartment_id or self.tenancy

        kw = {"timeout": TIMEOUT}
        self.compute = oci.core.ComputeClient(self.oci_config, **kw)
        self.identity = oci.identity.IdentityClient(self.oci_config, **kw)
        self.network = oci.core.VirtualNetworkClient(self.oci_config, **kw)
        self.storage = oci.core.BlockstorageClient(self.oci_config, **kw)

    # --- discovery -----------------------------------------------------------------

    def availability_domains(self) -> list[str]:
        ads = self.identity.list_availability_domains(self.tenancy).data
        return [ad.name for ad in ads]

    def regions(self) -> list[tuple[str, bool]]:
        """Subscribed regions as (name, is_home)."""
        subs = self.identity.list_region_subscriptions(self.tenancy).data
        return [(r.region_name, bool(r.is_home_region)) for r in subs if r.status == "READY"]

    def subnets(self) -> list[SubnetInfo]:
        vcns = list_call_get_all_results(self.network.list_vcns, self.compartment).data
        result = []
        for vcn in vcns:
            subnets = list_call_get_all_results(
                self.network.list_subnets, self.compartment, vcn_id=vcn.id
            ).data
            for s in subnets:
                if s.lifecycle_state != "AVAILABLE":
                    continue
                result.append(
                    SubnetInfo(
                        id=s.id,
                        name=s.display_name,
                        vcn_name=vcn.display_name,
                        cidr=s.cidr_block,
                        public=not s.prohibit_public_ip_on_vnic,
                    )
                )
        return result

    def get_subnet(self, subnet_id: str) -> Any:
        return self.network.get_subnet(subnet_id).data

    def images(self, operating_system: str | None = None, version: str | None = None) -> list[Any]:
        kwargs: dict[str, Any] = {
            "shape": self.cfg.shape,
            "lifecycle_state": "AVAILABLE",
            "sort_by": "TIMECREATED",
            "sort_order": "DESC",
        }
        if operating_system:
            kwargs["operating_system"] = operating_system
        if version:
            kwargs["operating_system_version"] = version
        return list_call_get_all_results(self.compute.list_images, self.compartment, **kwargs).data

    def resolve_image(self) -> Any:
        """Return the configured image_id, or the newest image matching `image`."""
        if self.cfg.image_id:
            return self.compute.get_image(self.cfg.image_id).data
        os_name, version = self.cfg.image_spec()
        images = self.images(os_name, version)
        # Skip GPU / special builds, prefer plain platform images.
        plain = [i for i in images if "GPU" not in (i.display_name or "")]
        if not (plain or images):
            raise LookupError(f"No '{self.cfg.image}' image for {self.cfg.shape} in {self.region}")
        return (plain or images)[0]

    def create_public_network(self, name: str = "a1-hunter") -> SubnetInfo:
        """VCN + internet gateway + default route + public subnet (like the console's VCN wizard).

        The VCN's default security list already allows SSH (22) from anywhere.
        """
        m = oci.core.models
        net = self.network
        vcn = net.create_vcn(
            m.CreateVcnDetails(
                compartment_id=self.compartment,
                cidr_blocks=["10.0.0.0/16"],
                display_name=f"{name}-vcn",
            )
        ).data
        vcn = oci.wait_until(net, net.get_vcn(vcn.id), "lifecycle_state", "AVAILABLE").data
        igw = net.create_internet_gateway(
            m.CreateInternetGatewayDetails(
                compartment_id=self.compartment,
                vcn_id=vcn.id,
                is_enabled=True,
                display_name=f"{name}-igw",
            )
        ).data
        oci.wait_until(net, net.get_internet_gateway(igw.id), "lifecycle_state", "AVAILABLE")
        route_table = net.get_route_table(vcn.default_route_table_id).data
        rules = [r for r in route_table.route_rules or [] if r.destination != "0.0.0.0/0"]
        rules.append(
            m.RouteRule(
                destination="0.0.0.0/0",
                destination_type="CIDR_BLOCK",
                network_entity_id=igw.id,
            )
        )
        net.update_route_table(route_table.id, m.UpdateRouteTableDetails(route_rules=rules))
        subnet = net.create_subnet(
            m.CreateSubnetDetails(
                compartment_id=self.compartment,
                vcn_id=vcn.id,
                cidr_block="10.0.0.0/24",
                display_name=f"{name}-public",
                prohibit_public_ip_on_vnic=False,
                route_table_id=vcn.default_route_table_id,
                security_list_ids=[vcn.default_security_list_id],
            )
        ).data
        subnet = oci.wait_until(net, net.get_subnet(subnet.id), "lifecycle_state", "AVAILABLE").data
        return SubnetInfo(subnet.id, subnet.display_name, vcn.display_name, subnet.cidr_block, True)

    # --- usage / existing instances ------------------------------------------------

    def live_instances(self) -> list[Any]:
        instances = list_call_get_all_results(self.compute.list_instances, self.compartment).data
        return [i for i in instances if i.lifecycle_state not in GONE_STATES]

    def find_instance(self, display_name: str) -> Any | None:
        for inst in self.live_instances():
            if inst.display_name == display_name and inst.shape == self.cfg.shape:
                return inst
        return None

    def usage(self, ads: list[str]) -> Usage:
        a1 = [i for i in self.live_instances() if i.shape == "VM.Standard.A1.Flex"]
        ocpus = sum((i.shape_config.ocpus or 0) for i in a1 if i.shape_config)
        mem = sum((i.shape_config.memory_in_gbs or 0) for i in a1 if i.shape_config)

        storage = 0
        for ad in ads:
            boots = list_call_get_all_results(
                self.storage.list_boot_volumes,
                availability_domain=ad,
                compartment_id=self.compartment,
            ).data
            storage += sum(
                b.size_in_gbs or 0 for b in boots if b.lifecycle_state not in GONE_STATES
            )
        vols = list_call_get_all_results(
            self.storage.list_volumes, compartment_id=self.compartment
        ).data
        storage += sum(v.size_in_gbs or 0 for v in vols if v.lifecycle_state not in GONE_STATES)
        return Usage(a1_ocpus=ocpus, a1_memory_gb=mem, storage_gb=storage, instances=a1)

    # --- launch --------------------------------------------------------------------

    def build_launch_details(self, image_id: str, ssh_key: str) -> Any:
        m = oci.core.models
        return m.LaunchInstanceDetails(
            compartment_id=self.compartment,
            display_name=self.cfg.display_name,
            shape=self.cfg.shape,
            shape_config=m.LaunchInstanceShapeConfigDetails(
                ocpus=float(self.cfg.ocpus), memory_in_gbs=float(self.cfg.memory_gb)
            ),
            source_details=m.InstanceSourceViaImageDetails(
                image_id=image_id, boot_volume_size_in_gbs=int(self.cfg.boot_volume_gb)
            ),
            create_vnic_details=m.CreateVnicDetails(
                subnet_id=self.cfg.subnet_id, assign_public_ip=self.cfg.assign_public_ip
            ),
            metadata={"ssh_authorized_keys": ssh_key},
        )

    def launch(self, details: Any, availability_domain: str) -> Any:
        details.availability_domain = availability_domain
        # No SDK-level retries: the hunt loop owns retry timing and backoff.
        return self.compute.launch_instance(details, retry_strategy=NO_RETRY).data

    def wait_running(self, instance_id: str, max_wait: int = 900) -> Any:
        resp = oci.wait_until(
            self.compute,
            self.compute.get_instance(instance_id),
            "lifecycle_state",
            "RUNNING",
            max_wait_seconds=max_wait,
        )
        return resp.data

    def public_ip(self, instance_id: str) -> str | None:
        atts = self.compute.list_vnic_attachments(self.compartment, instance_id=instance_id).data
        for att in atts:
            if att.lifecycle_state == "ATTACHED":
                vnic = self.network.get_vnic(att.vnic_id).data
                if vnic.public_ip:
                    return vnic.public_ip
        return None
