from unittest.mock import Mock

import pytest
from certbot import errors
from certbot.compat import filesystem

from certbot_dns_esa.dns_esa import Authenticator
from certbot_dns_esa.esa_client import ESAClient, Site
from certbot_dns_esa.names import normalize_name


def test_zone_label_boundary(helper):
    with pytest.raises(errors.PluginError, match="No managed"):
        helper._find_site("notexample.com")


def test_longest_managed_site_and_cached_discovery(helper):
    assert helper._find_site("_acme-challenge.sub.example.co.uk").site_id == 3
    assert helper._find_site("_acme-challenge.other.example.co.uk").site_id == 2
    assert helper._find_site("EXAMPLE.COM.").site_id == 1
    helper.client.list_sites.assert_called_once()


def test_cname_site_rejected_without_falling_back_to_parent(helper):
    helper.client.list_sites.return_value = [
        Site(1, "example.com", "NS"),
        Site(2, "sub.example.com", "CNAME"),
    ]
    with pytest.raises(errors.PluginError, match="does not use NS access"):
        helper.add_txt_record("_acme-challenge.sub.example.com", "token")
    helper.client.create_txt_record.assert_not_called()


def test_ambiguous_sites_rejected(helper):
    helper.client.list_sites.return_value = [
        Site(1, "example.com", "NS"),
        Site(2, "example.com", "NS"),
    ]
    with pytest.raises(errors.PluginError, match="Multiple ESA sites"):
        helper.add_txt_record("_acme-challenge.example.com", "token")
    helper.client.create_txt_record.assert_not_called()


def test_failed_discovery_can_be_retried(helper):
    helper.client.list_sites.side_effect = [
        errors.PluginError("denied"),
        [Site(1, "example.com", "NS")],
    ]
    with pytest.raises(errors.PluginError):
        helper._find_site("_acme-challenge.example.com")
    assert helper._sites is None
    assert helper._find_site("_acme-challenge.example.com").site_id == 1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("例子.公司.cn.", "xn--fsqu00a.xn--55qx5d.cn"),
        ("_ACME-CHALLENGE.Example.COM.", "_acme-challenge.example.com"),
        ("Example.COM", "example.com"),
    ],
)
def test_normalize_name(name, expected):
    assert normalize_name(name) == expected


@pytest.mark.parametrize(
    "name",
    ["", "*.example.com", "a..com", "a b.com", "a/host.com", "a" * 64 + ".com", "\ud800.com"],
)
def test_invalid_name(name):
    with pytest.raises(errors.PluginError):
        normalize_name(name)


def write_credentials(tmp_path, authenticator, text):
    path = tmp_path / "esa.ini"
    path.write_text(text, encoding="utf-8")
    filesystem.chmod(str(path), 0o600)
    authenticator.config.dns_esa_credentials = str(path)
    authenticator._helper = None
    return path


def test_real_credentials_and_client_reuse(tmp_path, authenticator, monkeypatch):
    factory = Mock()
    monkeypatch.setattr("certbot_dns_esa.dns_esa.ESAClient", factory)
    path = write_credentials(
        tmp_path,
        authenticator,
        """
dns_esa_access_key_id = id
dns_esa_access_key_secret = secret
dns_esa_region_id = ap-southeast-1
dns_esa_security_token = session
""",
    )
    authenticator._setup_credentials()
    saved = authenticator._helper
    authenticator._setup_credentials()
    assert authenticator._helper is saved
    assert authenticator.credentials.conf("access_key_id") == "id"
    assert authenticator.conf("credentials") == str(path)
    factory.assert_called_once_with("id", "secret", "ap-southeast-1", security_token="session")


def test_default_credentials(tmp_path, authenticator, monkeypatch):
    factory = Mock()
    monkeypatch.setattr("certbot_dns_esa.dns_esa.ESAClient", factory)
    write_credentials(
        tmp_path,
        authenticator,
        "dns_esa_access_key_id=id\ndns_esa_access_key_secret=secret",
    )
    authenticator._setup_credentials()
    factory.assert_called_once_with("id", "secret", "cn-hangzhou", security_token=None)
    assert authenticator._helper._sites is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "dns_esa_access_key_id=id",
        "dns_esa_access_key_id=id\ndns_esa_access_key_secret=",
        "dns_esa_access_key_id=\ndns_esa_access_key_secret=secret",
    ],
)
def test_invalid_credentials(tmp_path, authenticator, text):
    write_credentials(tmp_path, authenticator, text)
    with pytest.raises(errors.PluginError):
        authenticator._setup_credentials()


@pytest.mark.parametrize(
    ("key", "value"),
    [("dns_esa_ttl", 0), ("dns_esa_ttl", -1), ("dns_esa_propagation_seconds", -1)],
)
def test_invalid_options(authenticator, key, value):
    setattr(authenticator.config, key, value)
    with pytest.raises(errors.PluginError):
        authenticator._setup_credentials()


def test_unknown_region_rejected():
    with pytest.raises(errors.PluginError, match="region_id"):
        ESAClient("id", "secret", "cn-shanghai")


def test_unconfigured_authenticator(authenticator):
    authenticator._helper = None
    with pytest.raises(errors.PluginError, match="not been configured"):
        authenticator._perform("example.com", "_acme-challenge.example.com", "token")
    authenticator._cleanup("example.com", "_acme-challenge.example.com", "token")


def test_parser_defaults():
    added = {}
    Authenticator.add_parser_arguments(lambda name, **kw: added.update({name: kw}))
    assert added["propagation-seconds"]["default"] == 30
    assert added["ttl"]["default"] == 600
