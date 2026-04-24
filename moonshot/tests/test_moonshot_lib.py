import base64
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from moonshot_lib import (
    expand_host,
    load_credentials,
    make_headers,
    normalize_node,
    send_reboot,
)


class TestExpandHost:
    def test_integer_expands_to_full_hostname(self):
        assert expand_host("7") == "moon-chassis-7.inband.releng.mdc1.mozilla.com"

    def test_integer_string_single_digit(self):
        assert expand_host("1") == "moon-chassis-1.inband.releng.mdc1.mozilla.com"

    def test_integer_string_multi_digit(self):
        assert expand_host("12") == "moon-chassis-12.inband.releng.mdc1.mozilla.com"

    def test_full_hostname_passthrough(self):
        host = "moon-chassis-7.inband.releng.mdc1.mozilla.com"
        assert expand_host(host) == host

    def test_partial_hostname_passthrough(self):
        host = "moon-chassis-7"
        assert expand_host(host) == host


class TestNormalizeNode:
    def test_digit_only_expands(self):
        assert normalize_node("1") == "c1n1"

    def test_digit_only_multi_digit_expands(self):
        assert normalize_node("5") == "c5n1"

    def test_cart_only_expands(self):
        assert normalize_node("c1") == "c1n1"

    def test_cart_only_multi_digit_expands(self):
        assert normalize_node("c12") == "c12n1"

    def test_full_node_id_passthrough(self):
        assert normalize_node("c1n1") == "c1n1"

    def test_full_node_id_non_one_passthrough(self):
        assert normalize_node("c5n1") == "c5n1"


class TestMakeHeaders:
    def test_authorization_header_present(self):
        headers = make_headers("user", "pass")
        assert "Authorization" in headers

    def test_authorization_is_basic(self):
        headers = make_headers("user", "pass")
        assert headers["Authorization"].startswith("Basic ")

    def test_credentials_encoded_correctly(self):
        headers = make_headers("admin", "secret")
        encoded = headers["Authorization"].removeprefix("Basic ")
        decoded = base64.b64decode(encoded).decode("ascii")
        assert decoded == "admin:secret"

    def test_content_type_is_json(self):
        headers = make_headers("user", "pass")
        assert headers["Content-Type"] == "application/json"


class TestLoadCredentials:
    def test_loads_username_and_password(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".moonshot_ilo", delete=False) as f:
            f.write("myuser\nmypassword\n")
            tmp_path = f.name
        try:
            with patch("moonshot_lib.os.path.expanduser", return_value=os.path.dirname(tmp_path)):
                with patch("moonshot_lib.os.path.join", return_value=tmp_path):
                    username, password = load_credentials()
            assert username == "myuser"
            assert password == "mypassword"
        finally:
            os.unlink(tmp_path)

    def test_missing_file_exits(self):
        with patch("moonshot_lib.os.path.join", return_value="/nonexistent/path/.moonshot_ilo"):
            with pytest.raises(SystemExit):
                load_credentials()

    def test_strips_whitespace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".moonshot_ilo", delete=False) as f:
            f.write("  myuser  \n  mypassword  \n")
            tmp_path = f.name
        try:
            with patch("moonshot_lib.os.path.join", return_value=tmp_path):
                username, password = load_credentials()
            assert username == "myuser"
            assert password == "mypassword"
        finally:
            os.unlink(tmp_path)


class TestSendReboot:
    def _make_ok_response(self):
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.text = ""
        return response

    def _make_error_response(self):
        response = MagicMock()
        response.ok = False
        response.status_code = 500
        response.text = "Internal Server Error"
        return response

    def test_posts_to_correct_url(self):
        with patch("moonshot_lib.requests.post", return_value=self._make_ok_response()) as mock_post:
            send_reboot("https://host/rest/v1/Systems/c1n1", {})
            mock_post.assert_called_once()
            assert mock_post.call_args[0][0] == "https://host/rest/v1/Systems/c1n1"

    def test_sends_cold_reset_payload(self):
        with patch("moonshot_lib.requests.post", return_value=self._make_ok_response()) as mock_post:
            send_reboot("https://host/rest/v1/Systems/c1n1", {})
            payload = mock_post.call_args[1]["json"]
            assert payload["Action"] == "Reset"
            assert payload["ResetType"] == "ColdReset"

    def test_exits_on_error_response(self):
        with patch("moonshot_lib.requests.post", return_value=self._make_error_response()):
            with pytest.raises(SystemExit):
                send_reboot("https://host/rest/v1/Systems/c1n1", {})

    def test_verbose_does_not_raise(self, capsys):
        with patch("moonshot_lib.requests.post", return_value=self._make_ok_response()):
            send_reboot("https://host/rest/v1/Systems/c1n1", {"Authorization": "Basic x"}, verbose=True)
            out = capsys.readouterr().out
            assert "[VERBOSE]" in out
