# CARTO basemap key storage, kept in the operating system credential vault and never in a project
#
# Ported near-verbatim from COYPU (D:\MT\thesis-railway\basemap_key.py); the camelCase API is kept
# deliberately so re-syncing the two copies stays a plain diff. Feeder differences: its own vault
# entry, COYPU's entry read as a fallback (a key entered once in COYPU works here too), and no
# legacy settings key (the Feeder never stored the key in its settings).
import os
import re

try:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
except ImportError:
    keyring = None
    KeyringError = PasswordDeleteError = Exception

# Credential vault entry the key is stored under, Windows Credential Manager on Windows
KEYRING_SERVICE = "COYPU-Feeder"
KEYRING_ENTRY = "cartoBasemapKey"

# COYPU's own entry, only ever read, so a key stored there needs no second entry
COYPU_KEYRING_SERVICE = "COYPU"
COYPU_KEYRING_ENTRY = "cartoBasemapKey"

# Fallbacks for development and scripted runs, read only when neither vault entry holds a key
ENVIRONMENT_VARIABLE = "COYPU_FEEDER_MAP_API_KEY"
COYPU_ENVIRONMENT_VARIABLE = "COYPU_MAP_API_KEY"

# Where a user without a key of their own can ask the author for one
AUTHOR_CONTACT_URL = "https://github.com/surovskyjk"

# Where anyone can register a free key of their own
CARTO_KEY_SIGNUP_URL = "https://carto.com/basemaps/apikey/"

# Value of a key query parameter in any URL, masked before a message reaches the interface
KEY_PARAMETER_PATTERN = re.compile(r"([?&]key=)[^&\s\"'#]+")


# One vault entry, an empty string when it is missing or the vault fails
def readVaultEntry(service, entry):
    if keyring is None:
        return ""
    try:
        return keyring.get_password(service, entry) or ""
    except KeyringError:
        return ""


# Every place a key can come from, in the order loadKey trusts them
def keyCandidates():
    yield "feeder", readVaultEntry(KEYRING_SERVICE, KEYRING_ENTRY)
    yield "coypu", readVaultEntry(COYPU_KEYRING_SERVICE, COYPU_KEYRING_ENTRY)
    yield "environment", os.environ.get(ENVIRONMENT_VARIABLE, "")
    yield "environment", os.environ.get(COYPU_ENVIRONMENT_VARIABLE, "")


# Own stored key first, COYPU's second, the environment last, an empty string when none has one
def loadKey():
    return next((key.strip() for _, key in keyCandidates() if key.strip()), "")


# Where the key loadKey returns comes from: "feeder", "coypu", "environment", or "" for no key
def keySource():
    return next((source for source, key in keyCandidates() if key.strip()), "")


# Keep the key in the vault, reporting whether it actually got there
def saveKey(apiKey):
    if keyring is None:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_ENTRY, apiKey)
    except KeyringError:
        return False
    return True


# Remove the key from the vault, a key that was never stored counts as removed
def deleteKey():
    if keyring is None:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ENTRY)
    except PasswordDeleteError:
        return True
    except KeyringError:
        return False
    return True


# Mask every key query value in a text, so an error message never shows the key itself
def redactKey(text):
    return KEY_PARAMETER_PATTERN.sub(r"\1***", str(text))
