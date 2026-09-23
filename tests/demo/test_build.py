from collections import Counter
from decimal import Decimal
import json

import pytest

from backend.app.demo.build import write_run
from backend.app.demo.derive import derive
from backend.app.demo.generator import generate_source
from backend.app.demo.models import DemoSource, SourceGroup, SourceNode, SourceQuality, SourceTransfer
from backend.app.store import ResultStore


def small_source(outbound=False):
    return DemoSource(seed=1, groups=(SourceGroup(id="g", name="Группа"),), nodes=tuple(
        SourceNode(gid=gid, display_name="Александр Иванов", group_id="g",
                   quality=SourceQuality(outbound_censored=outbound, inbound_incomplete=False)) for gid in ("0007", "7")),
        transfers=(SourceTransfer(id="one", source="0007", target="7", amount_kzt="5000.01", date="2026-07-01"),))


def test_exported_run_matches_source(tmp_path):
    source = generate_source()
    write_run(source, tmp_path, "fixture-demo-test")
    store = ResultStore.from_directory(tmp_path)
    records = derive(source)
    assert len(records.nodes) == 500
    assert {n.role for n in records.nodes} == {"coordinator", "consolidator", "transit", "distributor", "terminal", "peripheral"}
    sizes = Counter(n.component_id for n in records.nodes)
    assert len(sizes) == 16 and max(sizes.values()) == 320
    assert sum(size == 1 for size in sizes.values()) == 8
    ledger_total = sum(Decimal(t.amount_kzt) for t in source.transfers)
    assert sum(Decimal(e.amount_kzt) for e in records.edges) == ledger_total
    assert sum(Decimal(n.metrics.in_amount_kzt) for n in records.nodes) == ledger_total
    assert sum(n.metrics.tx_in_count for n in records.nodes) == 6000
    assert store.run_id == "fixture-demo-test"
    assert [store.get_node(gid).role for gid in ("100001", "100010", "100020", "100030", "100040", "900001")] == [
        "coordinator", "consolidator", "transit", "distributor", "terminal", "peripheral"]
    assert store.get_node("900001").role_score == 0
    assert len(json.loads(store.get_export("node_names").content)) == 500
    assert json.loads(store.get_export("manifest").content)["run_id"] == store.run_id
    assert store.get_graph(component_id=store.get_node("100001").component_id).truncated


def test_repeated_and_reciprocal_transfers_are_exact():
    source = small_source()
    source = source.model_copy(update={"transfers": source.transfers + (
        SourceTransfer(id="two", source="0007", target="7", amount_kzt="6000.02", date="2026-07-02"),
        SourceTransfer(id="three", source="7", target="0007", amount_kzt="7000.03", date="2026-07-03"))})
    result = derive(source)
    assert [(e.source, e.target, e.amount_kzt, e.tx_count) for e in result.edges] == [("0007", "7", "11000.03", 2), ("7", "0007", "7000.03", 1)]
    by_gid = {n.gid: n for n in result.nodes}
    assert by_gid["0007"].metrics.out_amount_kzt == "11000.03"
    assert by_gid["7"].metrics.in_amount_kzt == "11000.03"
    assert by_gid["0007"].metrics.tx_in_count == 1
    assert by_gid["7"].metrics.tx_in_count == 2


@pytest.mark.parametrize("outbound,role", [(False, "terminal"), (True, "peripheral"), (None, "peripheral")])
def test_terminal_requires_known_complete_outgoing(outbound, role):
    assert next(n for n in derive(small_source(outbound)).nodes if n.gid == "7").role == role
