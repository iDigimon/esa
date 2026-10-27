"""ESA 2024-09-10 operations using the official Alibaba Cloud SDK."""

from dataclasses import dataclass
from typing import Optional

from alibabacloud_esa20240910 import models
from alibabacloud_esa20240910.client import Client
from alibabacloud_tea_openapi.utils_models import Config
from certbot import errors
from darabonba.exceptions import DaraException, UnretryableException
from darabonba.policy.retry import RetryOptions
from darabonba.runtime import RuntimeOptions

from certbot_dns_esa.names import normalize_name


@dataclass(frozen=True)
class Site:
    site_id: int
    name: str
    access_type: str


@dataclass(frozen=True)
class TXTRecord:
    record_id: int
    value: str


class ESAClient:
    """Manage individual TXT records; never replace an existing RRset."""

    page_size = 100
    regions = ("cn-hangzhou", "ap-southeast-1")

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        region_id: str = "cn-hangzhou",
        *,
        security_token: Optional[str] = None,
    ) -> None:
        if region_id not in self.regions:
            raise errors.PluginError("ESA region_id must be cn-hangzhou or ap-southeast-1")
        self.client = Client(
            Config(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                security_token=security_token,
                region_id=region_id,
                endpoint=f"esa.{region_id}.aliyuncs.com",
                # The current SDK uses Config.retry_options, not RuntimeOptions.autoretry.
                # Replaying a timed-out write could create a second record we do not own.
                retry_options=RetryOptions(retryable=False),
            )
        )
        self.runtime = RuntimeOptions(connect_timeout=10000, read_timeout=30000, autoretry=False)

    def _call(self, operation: str, request):
        try:
            body = getattr(self.client, operation)(request, self.runtime).body
        except (DaraException, UnretryableException) as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            # SDK messages can contain credentials and TXT values.
            raise errors.PluginError(f"Aliyun ESA {operation} failed ({code})") from exc
        if body is None:
            raise errors.PluginError(f"Aliyun ESA {operation} returned an empty response")
        return body

    def _pages(self, operation: str, request_type, items_attribute: str, **filters):
        page, received, total = 1, 0, None
        while True:
            body = self._call(
                operation,
                request_type(page_number=page, page_size=self.page_size, **filters),
            )
            items = getattr(body, items_attribute) or []
            count = body.total_count
            if (
                count is None
                or count < 0
                or (total is not None and count != total)
                or (body.page_number is not None and body.page_number != page)
                or (not items and received < count)
                or received + len(items) > count
            ):
                raise errors.PluginError(
                    f"Aliyun ESA returned an incomplete {items_attribute} list"
                )
            total = count
            received += len(items)
            yield from items
            if received == total:
                return
            page += 1

    def list_sites(self) -> list[Site]:
        sites = []
        seen = set()
        for item in self._pages("list_sites_with_options", models.ListSitesRequest, "sites"):
            if (
                not item.site_id
                or not item.site_name
                or not item.access_type
                or item.site_id in seen
            ):
                raise errors.PluginError("Aliyun ESA returned an invalid or duplicate site")
            seen.add(item.site_id)
            sites.append(Site(item.site_id, normalize_name(item.site_name), item.access_type))
        return sites

    def get_txt_records(self, site_id: int, record_name: str) -> list[TXTRecord]:
        record_name = normalize_name(record_name)
        records = []
        seen = set()
        for item in self._pages(
            "list_records_with_options",
            models.ListRecordsRequest,
            "records",
            site_id=site_id,
            record_name=record_name,
            record_match_type="exact",
            type="TXT",
        ):
            if not item.record_id or item.record_id in seen:
                raise errors.PluginError("Aliyun ESA returned an invalid or duplicate record")
            seen.add(item.record_id)
            if (
                item.record_type == "TXT"
                and item.record_name
                and normalize_name(item.record_name) == record_name
                and item.proxied is not True
                and (item.site_id is None or item.site_id == site_id)
            ):
                if item.data is None or item.data.value is None:
                    raise errors.PluginError("Aliyun ESA returned a TXT record without a value")
                records.append(TXTRecord(item.record_id, item.data.value))
        return records

    def create_txt_record(self, site_id: int, record_name: str, value: str, ttl: int) -> int:
        body = self._call(
            "create_record_with_options",
            models.CreateRecordRequest(
                site_id=site_id,
                record_name=normalize_name(record_name),
                type="TXT",
                data=models.CreateRecordRequestData(value=value),
                ttl=ttl,
                proxied=False,
            ),
        )
        if not body.record_id:
            raise errors.PluginError("Aliyun ESA did not return a new record ID")
        return body.record_id

    def delete_record(self, record_id: int) -> None:
        try:
            self._call(
                "delete_record_with_options", models.DeleteRecordRequest(record_id=record_id)
            )
        except errors.PluginError as exc:
            if getattr(exc.__cause__, "code", None) != "Record.NotFound":
                raise
