from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alibabacloud_esa20240910 import models
from alibabacloud_tea_openapi.exceptions import ClientException
from certbot import errors
from darabonba.exceptions import UnretryableException
from darabonba.policy.retry import RetryPolicyContext

from certbot_dns_esa.esa_client import ESAClient, Site, TXTRecord


def dto(cls, data):
    return SimpleNamespace(body=cls().from_map(data))


def sites(items, total, **extra):
    return dto(models.ListSitesResponseBody, {"TotalCount": total, "Sites": items, **extra})


def site(id_=1, name="example.com", access="NS"):
    return {"SiteId": id_, "SiteName": name, "AccessType": access}


def records(items, total, **extra):
    return dto(models.ListRecordsResponseBody, {"TotalCount": total, "Records": items, **extra})


def record(id_=101, name="host.example.com", value="token", type_="TXT", **extra):
    return {
        "RecordId": id_,
        "RecordName": name,
        "RecordType": type_,
        "Data": {"Value": value},
        "Ttl": 600,
        "Proxied": False,
        **extra,
    }


def test_all_site_pages(client):
    call = client.client.list_sites_with_options
    call.side_effect = [
        sites([site(), site(2, "Example.co.uk.")], 3),
        sites([site(3, "sub.example.co.uk", "CNAME")], 3),
    ]
    assert client.list_sites() == [
        Site(1, "example.com", "NS"),
        Site(2, "example.co.uk", "NS"),
        Site(3, "sub.example.co.uk", "CNAME"),
    ]
    assert [item.args[0].to_map() for item in call.call_args_list] == [
        {"PageNumber": 1, "PageSize": 2, "TagFilter": []},
        {"PageNumber": 2, "PageSize": 2, "TagFilter": []},
    ]


def test_all_record_pages_and_exact_name_filter(client):
    call = client.client.list_records_with_options
    call.side_effect = [
        records([record(), record(102, name="other.example.com")], 3),
        records([record(103, name="HOST.EXAMPLE.COM.", value="second")], 3),
    ]
    assert client.get_txt_records(1, "HOST.EXAMPLE.COM.") == [
        TXTRecord(101, "token"),
        TXTRecord(103, "second"),
    ]
    assert call.call_args.args[0].to_map() == {
        "SiteId": 1,
        "RecordName": "host.example.com",
        "RecordMatchType": "exact",
        "Type": "TXT",
        "PageNumber": 2,
        "PageSize": 2,
    }


def test_only_txt_records_at_requested_site_reused(client):
    client.client.list_records_with_options.return_value = records(
        [
            record(1, type_="CNAME"),
            record(2, Proxied=True),
            record(3, SiteId=2),
            record(4, RecordName=None),
        ],
        4,
    )
    assert client.get_txt_records(1, "host.example.com") == []


def test_empty_lists(client):
    client.client.list_sites_with_options.return_value = sites([], 0)
    client.client.list_records_with_options.return_value = records([], 0)
    assert client.list_sites() == []
    assert client.get_txt_records(1, "example.com") == []


@pytest.mark.parametrize("kind", ["sites", "records"])
@pytest.mark.parametrize("total", [10, None, -1])
def test_incomplete_pagination_fails(client, kind, total):
    if kind == "sites":
        client.client.list_sites_with_options.return_value = sites([], total)
        operation = client.list_sites
    else:
        client.client.list_records_with_options.return_value = records([], total)

        def operation():
            return client.get_txt_records(1, "host.example.com")

    with pytest.raises(errors.PluginError, match="incomplete"):
        operation()


def test_missing_final_page_does_not_succeed(client):
    client.client.list_sites_with_options.side_effect = [sites([site(), site(2)], 3), sites([], 3)]
    with pytest.raises(errors.PluginError, match="incomplete"):
        client.list_sites()


@pytest.mark.parametrize(
    "second",
    [sites([site(3)], 4), sites([site(3)], 3, PageNumber=1), sites([site(3), site(4)], 3)],
)
def test_inconsistent_pagination_rejected(client, second):
    client.client.list_sites_with_options.side_effect = [sites([site(), site(2)], 3), second]
    with pytest.raises(errors.PluginError, match="incomplete"):
        client.list_sites()


@pytest.mark.parametrize("item", [site(None), site(name=None), site(access=None)])
def test_malformed_sites_rejected(client, item):
    client.client.list_sites_with_options.return_value = sites([item], 1)
    with pytest.raises(errors.PluginError, match="invalid"):
        client.list_sites()


def test_duplicate_sites_rejected(client):
    client.client.list_sites_with_options.return_value = sites([site(), site()], 2)
    with pytest.raises(errors.PluginError, match="duplicate"):
        client.list_sites()


@pytest.mark.parametrize("items", [[record(None)], [record(), record()]])
def test_malformed_or_duplicate_records_rejected(client, items):
    client.client.list_records_with_options.return_value = records(items, len(items))
    with pytest.raises(errors.PluginError, match="invalid or duplicate"):
        client.get_txt_records(1, "host.example.com")


@pytest.mark.parametrize("data", [None, {}, {"Value": None}])
def test_missing_txt_value_rejected(client, data):
    client.client.list_records_with_options.return_value = records([record(Data=data)], 1)
    with pytest.raises(errors.PluginError, match="without a value"):
        client.get_txt_records(1, "host.example.com")


def test_create_delete_real_sdk_request_schema(client):
    client.client.create_record_with_options.return_value = dto(
        models.CreateRecordResponseBody, {"RecordId": 123}
    )
    assert client.create_txt_record(1, "_acme-challenge.example.com", "token", 600) == 123
    call = client.client.create_record_with_options
    assert call.call_args.args[0].to_map() == {
        "SiteId": 1,
        "RecordName": "_acme-challenge.example.com",
        "Type": "TXT",
        "Data": {"Value": "token"},
        "Ttl": 600,
        "Proxied": False,
    }
    assert call.call_args.args[1].autoretry is False
    assert call.call_args.args[1].connect_timeout == 10000
    assert call.call_args.args[1].read_timeout == 30000
    client.delete_record(123)
    assert client.client.delete_record_with_options.call_args.args[0].to_map() == {"RecordId": 123}


def test_create_requires_record_id(client):
    client.client.create_record_with_options.return_value = dto(models.CreateRecordResponseBody, {})
    with pytest.raises(errors.PluginError, match="record ID"):
        client.create_txt_record(1, "host.example.com", "token", 600)


@pytest.mark.parametrize(
    "operation", ["list_sites", "list_records", "create_record", "delete_record"]
)
def test_permission_errors_are_redacted(client, operation):
    getattr(client.client, operation + "_with_options").side_effect = ClientException(
        code="NoPermission", message="sensitive request details"
    )
    with pytest.raises(errors.PluginError, match="NoPermission") as exc:
        if operation == "list_sites":
            client.list_sites()
        elif operation == "list_records":
            client.get_txt_records(1, "host.example.com")
        elif operation == "create_record":
            client.create_txt_record(1, "host.example.com", "token", 600)
        else:
            client.delete_record(123)
    assert "sensitive" not in str(exc.value)


def test_already_deleted_record_is_success(client):
    client.client.delete_record_with_options.side_effect = ClientException(code="Record.NotFound")
    client.delete_record(123)


def test_empty_response(client):
    client.client.list_sites_with_options.return_value = SimpleNamespace(body=None)
    with pytest.raises(errors.PluginError, match="empty response"):
        client.list_sites()


def test_network_failure_is_a_plugin_error(client):
    client.client.list_sites_with_options.side_effect = UnretryableException(
        RetryPolicyContext(exception=OSError("sensitive network details"))
    )
    with pytest.raises(errors.PluginError, match="UnretryableException") as exc:
        client.list_sites()
    assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize("region", ["cn-hangzhou", "ap-southeast-1"])
def test_sdk_config_and_sts(monkeypatch, region):
    factory = Mock()
    monkeypatch.setattr("certbot_dns_esa.esa_client.Client", factory)
    ESAClient("id", "secret", region, security_token="session")
    config = factory.call_args.args[0]
    assert config.endpoint == f"esa.{region}.aliyuncs.com"
    assert config.region_id == region
    assert config.security_token == "session"
    assert config.retry_options.retryable is False


def test_sdk_transport_does_not_retry_writes(monkeypatch):
    # Exercise the actual SDK's retry loop, not just the RuntimeOptions flag.
    client = ESAClient("id", "secret")
    send = Mock(side_effect=OSError("sensitive connection failure"))
    monkeypatch.setattr("darabonba.core.DaraCore.do_action", send)
    with pytest.raises(errors.PluginError, match="UnretryableException"):
        client.create_txt_record(1, "_acme-challenge.example.com", "token", 600)
    send.assert_called_once()
