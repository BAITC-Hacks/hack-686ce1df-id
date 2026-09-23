from collections import Counter

from backend.app.demo.generator import generate_source
from backend.app.demo.derive import derive


def test_seed_reproduces_names_and_ledger():
    first = generate_source()
    assert first == generate_source()
    assert len(first.nodes) == 500
    assert len(first.transfers) == 6000
    assert {"0007", "7", "100001", "100010", "100020", "100030", "100040", "900001"} <= {n.gid for n in first.nodes}
    assert all(n.display_name.strip() for n in first.nodes)
    assert 200 <= len({n.display_name for n in first.nodes}) < len(first.nodes)
    assert first != generate_source(42)


def test_background_has_sparse_channels_and_varied_roles():
    records = derive(generate_source())
    roles = Counter(node.role for node in records.nodes)
    assert 480 <= len(records.edges) <= 1200
    assert max(roles.values()) < 400
    assert all(count >= 5 for count in roles.values())
