"""Phone-name resolution and the watchdog."""
from android_use import devices, watchdog
from android_use.models import DeviceStatus

from conftest import write_config

TWO = {
    "devices": {
        "mine": {"host": "100.1.1.1", "token": "a", "label": "My phone"},
        "grandma": {"host": "100.2.2.2", "token": "b", "label": "Amma's phone"},
    },
}


def test_single_phone_needs_no_name(isolated_config):
    write_config(isolated_config, {"devices": {"mine": TWO["devices"]["mine"]}})
    phone, err = devices.resolve("")
    assert phone.name == "mine" and not err


def test_exact_partial_and_generic_names(isolated_config):
    write_config(isolated_config, {**TWO, "default_device": "mine"})
    assert devices.resolve("grandma")[0].name == "grandma"
    assert devices.resolve("amma")[0].name == "grandma"          # partial label
    assert devices.resolve("my phone")[0].name == "mine"          # exact label
    assert devices.resolve("the phone")[0].name == "mine"         # generic -> default


def test_ambiguity_is_refused(isolated_config):
    write_config(isolated_config, TWO)
    phone, err = devices.resolve("")
    assert phone is None and "none is set as the default" in err
    phone, err = devices.resolve("phone")                          # matches both labels
    assert phone is None and "more than one" in err


def test_lock_refuses_other_phones(isolated_config):
    write_config(isolated_config, {**TWO, "locked_device": "grandma"})
    assert devices.resolve("")[0].name == "grandma"
    phone, err = devices.resolve("mine")
    assert phone is None and "locked" in err


def test_watchdog_checks_every_phone_and_alerts_per_phone(isolated_config, monkeypatch):
    write_config(isolated_config, TWO)
    from android_use.app_backend import AppBackend

    def status(self):
        if "100.2.2.2" in self.base:
            return DeviceStatus(connected=False, detail="The accessibility service is off")
        return DeviceStatus(connected=True, transport="app")

    monkeypatch.setattr(AppBackend, "status", status)
    alerts = []
    monkeypatch.setattr(watchdog, "notify", lambda title, msg: alerts.append(title))
    health = watchdog.run_once(quiet=True)
    assert not health.ok and "grandma" in health.summary
    assert sorted(alerts) == ["grandma: unreachable", "mine: back online"]
    # No change, no new alerts.
    watchdog.run_once(quiet=True)
    assert len(alerts) == 2
    report = watchdog.last_report()
    assert "grandma: DOWN" in report and "mine: OK" in report
    assert watchdog.check_all()["grandma"].kind == "service_off"


def test_watchdog_without_phones_uses_adb(isolated_config, monkeypatch):
    class FakeAdb:
        def status(self):
            return DeviceStatus(connected=False, detail="No Android device found.")

    monkeypatch.setattr(watchdog, "adb_fallback", lambda: FakeAdb())
    results = watchdog.check_all()
    assert list(results) == ["adb"] and results["adb"].kind == "unreachable"


def test_forgetting_the_default_does_not_promote_another(isolated_config):
    write_config(isolated_config, {**TWO, "devices": {**TWO["devices"],
                 "dad": {"host": "100.3.3.3", "token": "c"}}, "default_device": "mine"})
    assert devices.remove_phone("mine")
    phone, err = devices.resolve("")
    assert phone is None and "none is set as the default" in err


def test_missing_adb_is_explained_not_crashed(monkeypatch):
    from android_use import adb, server, wireless
    import pytest

    monkeypatch.setattr(adb, "ADB", "/nonexistent/adb")
    with pytest.raises(adb.AdbError, match="Could not run adb"):
        adb.list_devices()
    ok, msg = wireless.ensure_connected()
    assert not ok and "Could not run adb" in msg
    # Through a tool: a sentence the user can act on, not a traceback.
    out = server.get_screen()
    assert "Phone not reachable" in out and "adb" in out


def test_hung_adb_is_explained(monkeypatch):
    import subprocess
    from android_use import adb
    import pytest

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=30)

    monkeypatch.setattr(adb, "ADB", "/usr/bin/adb")
    monkeypatch.setattr(adb.subprocess, "run", hang)
    with pytest.raises(adb.AdbError, match="did not answer"):
        adb.shell("echo hi")
