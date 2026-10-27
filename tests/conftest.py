"""Mock external API boundaries; retain actual Certbot configuration and SDK DTOs."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from certbot_dns_esa.dns_esa import Authenticator, _ESAHelper
from certbot_dns_esa.esa_client import ESAClient, Site


@pytest.fixture
def client(monkeypatch):
    sdk = Mock()
    monkeypatch.setattr("certbot_dns_esa.esa_client.Client", Mock(return_value=sdk))
    result = ESAClient("id", "secret", security_token="session")
    result.page_size = 2
    return result


@pytest.fixture
def helper(monkeypatch):
    cloud = Mock(spec=ESAClient)
    cloud.list_sites.return_value = [
        Site(1, "example.com", "NS"),
        Site(2, "example.co.uk", "NS"),
        Site(3, "sub.example.co.uk", "NS"),
    ]
    cloud.get_txt_records.return_value = []
    cloud.create_txt_record.side_effect = [101, 102, 103]
    monkeypatch.setattr("certbot_dns_esa.dns_esa.ESAClient", Mock(return_value=cloud))
    return _ESAHelper("id", "secret", "cn-hangzhou", 600)


@pytest.fixture
def authenticator(helper):
    config = SimpleNamespace(
        dns_esa_credentials=None,
        dns_esa_ttl=600,
        dns_esa_propagation_seconds=0,
        noninteractive_mode=True,
    )
    result = Authenticator(config, "dns-esa")
    result._helper = helper
    return result
