"""Certbot DNS-01 authentication for domains hosted on Alibaba Cloud ESA."""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from certbot import errors
from certbot.plugins import dns_common

from certbot_dns_esa.compat import DNSAuthenticator
from certbot_dns_esa.esa_client import ESAClient, Site
from certbot_dns_esa.names import normalize_name

logger = logging.getLogger(__name__)


class Authenticator(DNSAuthenticator):
    """Use Certbot's public lifecycle, credentials and file permission checks."""

    description = "通过阿里云边缘安全加速 ESA API 完成 DNS-01 验证，支持泛域名"
    ttl = 600

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.credentials: Optional[dns_common.CredentialsConfiguration] = None
        self._helper: Optional[_ESAHelper] = None

    @classmethod
    def add_parser_arguments(
        cls, add: Callable[..., None], default_propagation_seconds: int = 30
    ) -> None:
        super().add_parser_arguments(add, default_propagation_seconds)
        add("credentials", help="阿里云 ESA API 凭证 INI 文件路径")
        add("ttl", type=int, default=600, help="TXT 记录 TTL 秒数（默认 600，1 表示自动）")

    def more_info(self) -> str:
        return "在 NS 接入的 ESA 站点创建独立的 TXT 验证记录，仅清理本次创建的记录。"

    def _setup_credentials(self) -> None:
        ttl = self.conf("ttl")
        propagation = self.conf("propagation-seconds")
        if ttl is None or ttl < 1:
            raise errors.PluginError("--dns-esa-ttl must be at least 1")
        if propagation is None or propagation < 0:
            raise errors.PluginError("--dns-esa-propagation-seconds must be non-negative")
        if self._helper is not None:
            return
        self.credentials = self._configure_credentials(
            "credentials",
            "阿里云 ESA API 凭证文件路径",
            {
                "access_key_id": "阿里云 AccessKey ID",
                "access_key_secret": "阿里云 AccessKey Secret",
            },
        )
        self.ttl = ttl
        self._helper = _ESAHelper(
            self.credentials.conf("access_key_id"),
            self.credentials.conf("access_key_secret"),
            self.credentials.conf("region_id") or "cn-hangzhou",
            ttl,
            security_token=self.credentials.conf("security_token"),
        )

    def _perform(self, domain: str, validation_name: str, validation: str) -> None:
        if self._helper is None:
            raise errors.PluginError("Aliyun ESA credentials have not been configured")
        self._helper.add_txt_record(validation_name, validation)

    def _cleanup(self, domain: str, validation_name: str, validation: str) -> None:
        if self._helper is not None:
            self._helper.del_txt_record(validation_name, validation)


@dataclass
class _RecordLease:
    record_id: int
    created: bool
    users: int = 1


class _ESAHelper:
    """Track ownership by normalized name and value across all challenges."""

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        region_id: str,
        ttl: int,
        *,
        security_token: Optional[str] = None,
    ) -> None:
        self.client = ESAClient(
            access_key_id, access_key_secret, region_id, security_token=security_token
        )
        self.ttl = ttl
        self._sites: Optional[list[Site]] = None
        self._leases: dict[tuple[str, str], _RecordLease] = {}

    def _find_site(self, record_name: str) -> Site:
        record_name = normalize_name(record_name)
        if self._sites is None:
            # Only cache complete discovery; never guess after an API failure.
            self._sites = self.client.list_sites()
        matches = [
            site
            for site in self._sites
            if record_name == site.name or record_name.endswith("." + site.name)
        ]
        if not matches:
            raise errors.PluginError(
                f"No managed ESA site found for {record_name}; check the credentials and region_id"
            )
        longest = max(len(site.name.split(".")) for site in matches)
        matches = [site for site in matches if len(site.name.split(".")) == longest]
        if len(matches) != 1:
            raise errors.PluginError(f"Multiple ESA sites match {record_name}")
        site = matches[0]
        if site.access_type != "NS":
            raise errors.PluginError(
                f"ESA site {site.name} does not use NS access; "
                "configure DNS-01 with its authoritative DNS provider"
            )
        return site

    def add_txt_record(self, record_name: str, record_content: str) -> None:
        record_name = normalize_name(record_name)
        key = (record_name, record_content)
        if key in self._leases:
            self._leases[key].users += 1
            return
        site = self._find_site(record_name)
        records = self.client.get_txt_records(site.site_id, record_name)
        existing = next((record for record in records if record.value == record_content), None)
        record_id = (
            existing.record_id
            if existing
            else self.client.create_txt_record(site.site_id, record_name, record_content, self.ttl)
        )
        self._leases[key] = _RecordLease(record_id, created=existing is None)
        logger.info("TXT challenge ready at %s", record_name)
        # Certbot waits once, after all challenges have been prepared.

    def del_txt_record(self, record_name: str, record_content: str) -> None:
        key = (normalize_name(record_name), record_content)
        lease = self._leases.get(key)
        if lease is None:
            return
        if lease.users == 1 and lease.created:
            try:
                self.client.delete_record(lease.record_id)
            except errors.PluginError as exc:
                logger.warning("Unable to clean TXT record at %s: %s", key[0], exc)
                # Keep the saved ID so a later cleanup can retry safely.
                return
            logger.info("Cleaned TXT challenge at %s", key[0])
        lease.users -= 1
        if lease.users == 0:
            del self._leases[key]
