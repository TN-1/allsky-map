import subprocess
import os

INSTALL_SH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../services/install.sh"))

def test_ensure_url_scheme():
    def test_val(initial_val):
        cmd = f'source "{INSTALL_SH}" && TEST_VAR="{initial_val}" && ensure_url_scheme TEST_VAR >/dev/null && echo -n "$TEST_VAR"'
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        return res.stdout

    assert test_val("srv1-vps.hamishwest.xyz") == "https://srv1-vps.hamishwest.xyz"
    assert test_val("https://srv1-vps.hamishwest.xyz") == "https://srv1-vps.hamishwest.xyz"
    assert test_val("http://srv1-vps.hamishwest.xyz") == "http://srv1-vps.hamishwest.xyz"
    assert test_val("") == ""

def test_validate_url():
    def check_url(url):
        cmd = f'source "{INSTALL_SH}" && validate_url "Test URL" "{url}"'
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        return res.returncode == 0

    assert check_url("https://srv1-vps.hamishwest.xyz") is True
    assert check_url("http://map.example.com") is True
    assert check_url("") is True  # empty is skipped
    assert check_url("localhost") is False
    assert check_url("not-a-url") is False

def test_validate_coord():
    def check_coord(val, min_val, max_val):
        cmd = f'source "{INSTALL_SH}" && validate_coord "Coord" "{val}" "{min_val}" "{max_val}"'
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        return res.returncode == 0

    assert check_coord("0.0", -90, 90) is True
    assert check_coord("-34.92", -90, 90) is True
    assert check_coord("90.0", -90, 90) is True
    assert check_coord("-90.0", -90, 90) is True
    assert check_coord("90.1", -90, 90) is False
    assert check_coord("-90.1", -90, 90) is False
    assert check_coord("abc", -90, 90) is False
