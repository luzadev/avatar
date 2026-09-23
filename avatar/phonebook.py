"""Rubrica: numeri di telefono ed email → nomi, dai Contatti di macOS (cache in memoria)."""
from __future__ import annotations

import re
import threading
import time

_map: dict[str, str] = {}
_loaded_at = 0.0
_lock = threading.Lock()


def normalize(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("3"):
        digits = "39" + digits
    return digits


def _load() -> None:
    global _loaded_at
    import Contacts
    store = Contacts.CNContactStore.alloc().init()
    keys = [Contacts.CNContactGivenNameKey, Contacts.CNContactFamilyNameKey, Contacts.CNContactOrganizationNameKey,
            Contacts.CNContactPhoneNumbersKey, Contacts.CNContactEmailAddressesKey]
    req = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(keys)
    new: dict[str, str] = {}

    def visit(c, stop):
        name = f"{c.givenName()} {c.familyName()}".strip() or c.organizationName()
        if not name:
            return
        for ph in c.phoneNumbers():
            d = normalize(ph.value().stringValue())
            if len(d) >= 8:
                new[d] = name
        for e in c.emailAddresses():
            new[str(e.value()).lower()] = name

    store.enumerateContactsWithFetchRequest_error_usingBlock_(req, None, visit)
    _map.clear()
    _map.update(new)
    _loaded_at = time.time()


def name_for(handle: str) -> str:
    """Nome del contatto per un numero o un'email; altrimenti il valore originale."""
    with _lock:
        if time.time() - _loaded_at > 3600:
            try:
                _load()
            except Exception:
                pass
    h = (handle or "").strip()
    if "@" in h:
        return _map.get(h.lower(), h)
    d = normalize(h)
    return _map.get(d) or _map.get(d[-10:]) or h
