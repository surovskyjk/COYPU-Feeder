"""
CARTO key storage: the key lives in the credential vault, the environment is only a fallback,
a broken vault degrades to no key, and no message ever shows the key itself.

Ported from COYPU's tests/test_basemap_key.py, plus the Feeder-only rule: COYPU's own vault
entry is read as a fallback but never written.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import basemap_key  # noqa: E402

ENV_VARS = (basemap_key.ENVIRONMENT_VARIABLE, basemap_key.COYPU_ENVIRONMENT_VARIABLE)
COYPU_ENTRY = (basemap_key.COYPU_KEYRING_SERVICE, basemap_key.COYPU_KEYRING_ENTRY)
FEEDER_ENTRY = (basemap_key.KEYRING_SERVICE, basemap_key.KEYRING_ENTRY)


# In memory stand in for the operating system vault, so no test touches the real one
class FakeKeyring:
    def __init__(self, isBroken=False):
        self.entries = {}
        self.isBroken = isBroken

    def failIfBroken(self):
        if self.isBroken:
            raise basemap_key.KeyringError("vault unavailable")

    def get_password(self, service, entry):
        self.failIfBroken()
        return self.entries.get((service, entry))

    def set_password(self, service, entry, value):
        self.failIfBroken()
        self.entries[(service, entry)] = value

    def delete_password(self, service, entry):
        self.failIfBroken()
        if (service, entry) not in self.entries:
            raise basemap_key.PasswordDeleteError("nothing stored")
        del self.entries[(service, entry)]


@pytest.fixture
def fakeVault(monkeypatch):
    vault = FakeKeyring()
    monkeypatch.setattr(basemap_key, "keyring", vault)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return vault


def testAStoredKeyIsReadBack(fakeVault):
    assert basemap_key.saveKey("stored123")
    assert basemap_key.loadKey() == "stored123"
    assert basemap_key.keySource() == "feeder"
    assert fakeVault.entries == {FEEDER_ENTRY: "stored123"}


def testDeletingRemovesTheKeyAndAMissingKeyCountsAsRemoved(fakeVault):
    basemap_key.saveKey("stored123")
    assert basemap_key.deleteKey()
    assert basemap_key.loadKey() == ""
    assert basemap_key.keySource() == ""
    assert basemap_key.deleteKey()


def testTheEnvironmentIsOnlyAFallback(fakeVault, monkeypatch):
    monkeypatch.setenv(basemap_key.ENVIRONMENT_VARIABLE, " fromEnvironment ")
    assert basemap_key.loadKey() == "fromEnvironment"
    assert basemap_key.keySource() == "environment"
    basemap_key.saveKey("stored123")
    assert basemap_key.loadKey() == "stored123"


def testCoypusEnvironmentVariableIsTheLastFallback(fakeVault, monkeypatch):
    monkeypatch.setenv(basemap_key.COYPU_ENVIRONMENT_VARIABLE, "coypuEnv")
    assert basemap_key.loadKey() == "coypuEnv"
    monkeypatch.setenv(basemap_key.ENVIRONMENT_VARIABLE, "feederEnv")
    assert basemap_key.loadKey() == "feederEnv"


def testCoypusVaultEntryIsAFallbackOnly(fakeVault, monkeypatch):
    fakeVault.entries[COYPU_ENTRY] = "coypuKey"
    monkeypatch.setenv(basemap_key.ENVIRONMENT_VARIABLE, "fromEnvironment")
    assert basemap_key.loadKey() == "coypuKey"      # COYPU's vault beats the environment
    assert basemap_key.keySource() == "coypu"
    basemap_key.saveKey("feederKey")
    assert basemap_key.loadKey() == "feederKey"     # the Feeder's own entry wins
    assert basemap_key.keySource() == "feeder"


def testSavingAndDeletingNeverTouchCoypusEntry(fakeVault):
    fakeVault.entries[COYPU_ENTRY] = "coypuKey"
    basemap_key.saveKey("feederKey")
    assert fakeVault.entries[COYPU_ENTRY] == "coypuKey"
    assert basemap_key.deleteKey()
    assert fakeVault.entries == {COYPU_ENTRY: "coypuKey"}
    assert basemap_key.loadKey() == "coypuKey"      # removing ours falls back to COYPU's


def testABrokenVaultDegradesToNoKey(monkeypatch):
    monkeypatch.setattr(basemap_key, "keyring", FakeKeyring(isBroken=True))
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert basemap_key.loadKey() == ""
    assert not basemap_key.saveKey("stored123")
    assert not basemap_key.deleteKey()


def testAMissingKeyringPackageDegradesToNoKey(monkeypatch):
    monkeypatch.setattr(basemap_key, "keyring", None)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert basemap_key.loadKey() == ""
    assert not basemap_key.saveKey("stored123")


def testRedactionMasksEveryKeyValue():
    text = ("https://a.basemaps.cartocdn.com/x.png?key=secret1 and "
            "https://b.basemaps.cartocdn.com/y.png?z=1&key=secret2#frag")
    redacted = basemap_key.redactKey(text)
    assert "secret1" not in redacted and "secret2" not in redacted
    assert redacted.count("key=***") == 2
