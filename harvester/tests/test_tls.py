import datetime as dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from uredni_desky import tls


def _cert(name, key, issuer_name, issuer_key, ca):
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    return (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .sign(issuer_key, hashes.SHA256()))


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def test_only_chain_to_trusted_root_is_accepted(monkeypatch):
    root_k, inter_k, leaf_k, evil_k = _key(), _key(), _key(), _key()
    root = _cert("Root CA", root_k, "Root CA", root_k, True)
    inter = _cert("Inter CA", inter_k, "Root CA", root_k, True)
    leaf = _cert("obec.cz", leaf_k, "Inter CA", inter_k, False)
    monkeypatch.setattr(tls, "_roots", [root])

    assert tls.verified_chain(leaf, [inter])

    # útočník na cestě: vlastní certifikát serveru odkazuje na vlastní autoritu s vlastním podpisem
    evil_root = _cert("Evil CA", evil_k, "Evil CA", evil_k, True)
    evil_leaf = _cert("obec.cz", leaf_k, "Evil CA", evil_k, False)
    assert not tls.verified_chain(evil_leaf, [evil_root])
    # … nebo „mezilehlou“ autoritu, kterou nepodepsal žádný důvěryhodný kořen
    evil_inter = _cert("Inter CA", evil_k, "Root CA", evil_k, True)
    fake_leaf = _cert("obec.cz", leaf_k, "Inter CA", evil_k, False)
    assert not tls.verified_chain(fake_leaf, [evil_inter])
    # mezilehlý certifikát, který není CA, ani prázdný řetězec se nepřijmou
    not_ca = _cert("Inter CA", inter_k, "Root CA", root_k, False)
    assert not tls.verified_chain(leaf, [not_ca])
    assert not tls.verified_chain(leaf, [])
