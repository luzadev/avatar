"""Contatti di macOS: cerca numero, email e dati di una persona (framework Contacts)."""
from __future__ import annotations

import threading

_store = None
_lock = threading.Lock()


def _get_store():
    global _store
    with _lock:
        if _store is not None:
            return _store
        import Contacts
        store = Contacts.CNContactStore.alloc().init()
        done, res = threading.Event(), {}

        def cb(granted, err):
            res["granted"] = bool(granted)
            done.set()

        store.requestAccessForEntityType_completionHandler_(Contacts.CNEntityTypeContacts, cb)
        done.wait(60)
        if not res.get("granted"):
            raise RuntimeError("Accesso ai Contatti negato: concedilo in Impostazioni di Sistema > Privacy e sicurezza > Contatti.")
        _store = store
        return store


def cerca(params: dict, ctx: dict) -> str:
    import Contacts
    q = str(params.get("nome", "")).strip()
    if not q:
        return "Errore: serve il nome."
    store = _get_store()
    keys = [Contacts.CNContactGivenNameKey, Contacts.CNContactFamilyNameKey, Contacts.CNContactOrganizationNameKey,
            Contacts.CNContactPhoneNumbersKey, Contacts.CNContactEmailAddressesKey]
    pred = Contacts.CNContact.predicateForContactsMatchingName_(q)
    people, err = store.unifiedContactsMatchingPredicate_keysToFetch_error_(pred, keys, None)
    if err:
        return f"Errore: {err}"
    people = list(people or [])[:6]
    if not people:
        return f"Nessun contatto trovato per '{q}'."
    lines = []
    for p in people:
        name = f"{p.givenName()} {p.familyName()}".strip() or p.organizationName()
        bits = [name]
        tels = [ph.value().stringValue() for ph in p.phoneNumbers()]
        mails = [str(e.value()) for e in p.emailAddresses()]
        if tels:
            bits.append("tel " + ", ".join(tels))
        if mails:
            bits.append("email " + ", ".join(mails))
        if p.organizationName() and p.organizationName() != name:
            bits.append(p.organizationName())
        lines.append("- " + " · ".join(bits))
    return "\n".join(lines)


TOOLS = [
    {"name": "contatti_cerca", "description": "Cerca una persona in Contatti e restituisce telefono, email e azienda. Usalo per trovare l'indirizzo email o il numero prima di scrivere a qualcuno per nome.",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]}, "run": cerca},
]
