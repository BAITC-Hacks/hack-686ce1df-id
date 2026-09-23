"""Contract boundary tests using synthetic, read-only Store doubles."""

import asyncio
import copy
import math
import time
import unittest

from backend.app.ai.tools import (
    RequestError,
    ToolError,
    execute_tool,
    require_run,
    to_data,
    tool_schemas,
    validate_arguments,
)


class SyntheticStore:
    """Development data only; concentration is supplied, not recalculated here."""

    run_id = "synthetic-run"

    def __init__(self):
        self.calls = []
        self.nodes = {"0007": {"gid": "0007"}, "7": {"gid": "7"}}
        self.cluster = {
            "cluster_id": "cluster-1",
            "component_id": "component-1",
            "gids": ["0007", "7"],
            "hypothesis": {"text": "Синтетический пример", "basis_rule_ids": [], "limitations": []},
        }
        self.graph = {
            "contract_version": "1.0",
            "run_id": self.run_id,
            "nodes": list(self.nodes.values()),
            "edges": [{"source": "0007", "target": "7", "amount_kzt": "70.00", "tx_count": 1}],
            "total_nodes": 2,
            "shown_nodes": 2,
            "truncated": False,
        }
        self.concentration = {
            "gid": "0007",
            "total_out_kzt": "100.00",
            "top_receiver_gid": "7",
            "top_receiver_share": 0.7,
            "receiver_count": 2,
        }

    def get_node(self, gid):
        self.calls.append(("get_node", {"gid": gid}))
        return self.nodes[gid]

    def get_neighbors(self, gid, radius=1, limit=50):
        self.calls.append(("get_neighbors", {"gid": gid, "radius": radius, "limit": limit}))
        return self.graph

    def get_cluster(self, cluster_id):
        self.calls.append(("get_cluster", {"cluster_id": cluster_id}))
        if cluster_id != self.cluster["cluster_id"]:
            raise KeyError(cluster_id)
        return self.cluster

    def check_concentration(self, gid):
        self.calls.append(("check_concentration", {"gid": gid}))
        return self.concentration


class ArgumentTests(unittest.TestCase):
    def test_gid_is_preserved_and_json_arguments_are_supported(self):
        self.assertEqual(validate_arguments("get_node", '{"gid":"0007"}'), {"gid": "0007"})

    def test_default_neighbors_parameters(self):
        args = {"gid": "0007"}
        self.assertEqual(validate_arguments("get_neighbors", args), {"gid": "0007", "radius": 1, "limit": 50})
        self.assertEqual(args, {"gid": "0007"})

    def test_rejects_extra_and_incorrect_arguments(self):
        cases = [
            ("get_node", {"gid": 7}),
            ("get_node", {"gid": "0007", "run_id": "injected"}),
            ("get_node", {"gid": ""}),
            ("get_node", {"gid": "   "}),
            ("get_cluster", {"gid": "0007"}),
            ("get_neighbors", {"gid": "0007", "radius": True}),
            ("get_neighbors", {"gid": "0007", "radius": 1.0}),
            ("get_neighbors", {"gid": "0007", "radius": 3}),
            ("get_neighbors", {"gid": "0007", "limit": False}),
            ("get_neighbors", {"gid": "0007", "limit": 0}),
            ("get_neighbors", {"gid": "0007", "limit": 301}),
            ("get_node", '["0007"]'),
            ("get_node", '{"gid":"0007","gid":"7"}'),
            ("get_node", '{"gid":NaN}'),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args), self.assertRaises(ToolError) as caught:
                validate_arguments(name, args)
            self.assertEqual(caught.exception.code, "invalid_arguments")

    def test_schema_has_exactly_four_strict_tools(self):
        schemas = tool_schemas()
        self.assertEqual({item["name"] for item in schemas}, {"get_node", "get_neighbors", "get_cluster", "check_concentration"})
        for schema in schemas:
            self.assertTrue(schema["strict"])
            params = schema["parameters"]
            self.assertFalse(params["additionalProperties"])
            self.assertEqual(set(params["required"]), set(params["properties"]))
        neighbors = next(item for item in schemas if item["name"] == "get_neighbors")
        self.assertEqual(neighbors["parameters"]["properties"]["radius"]["type"], "integer")

    def test_schemas_are_independent_copies(self):
        schemas = tool_schemas()
        schemas[0]["parameters"]["properties"].clear()
        self.assertTrue(tool_schemas()[0]["parameters"]["properties"])


class SerializationTests(unittest.TestCase):
    def test_returns_deep_copy(self):
        source = {"nested": [{"value": "100.00"}]}
        result = to_data(source)
        result["nested"][0]["value"] = "0.00"
        self.assertEqual(source["nested"][0]["value"], "100.00")

    def test_accepts_pydantic_style_model(self):
        class Model:
            def model_dump(self, *, mode):
                self.mode = mode
                return {"gid": "0007"}

        model = Model()
        self.assertEqual(to_data(model), {"gid": "0007"})
        self.assertEqual(model.mode, "json")

    def test_rejects_non_json_and_nonfinite_values(self):
        for value in [[], {1: "x"}, {"a": object()}, {"a": [math.nan]}, {"a": math.inf}, {"a": -math.inf}]:
            with self.subTest(value=value), self.assertRaises(ToolError):
                to_data(value)


class RunTests(unittest.TestCase):
    def test_run_missing_and_stale_have_http_statuses(self):
        store = SyntheticStore()
        with self.assertRaises(RequestError) as stale:
            require_run(store, "previous-run")
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(stale.exception.run_id, "synthetic-run")
        with self.assertRaises(RequestError) as malformed:
            require_run(store, 7)
        self.assertEqual(malformed.exception.status_code, 422)
        store.run_id = None
        with self.assertRaises(RequestError) as absent:
            require_run(store, "synthetic-run")
        self.assertEqual(absent.exception.status_code, 503)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SyntheticStore()
        self.run_id = self.store.run_id

    async def test_unknown_tool_has_zero_store_dispatches(self):
        with self.assertRaises(ToolError) as caught:
            await execute_tool("delete_node", {"gid": "0007"}, self.store, self.run_id)
        self.assertEqual(caught.exception.code, "unknown_tool")
        self.assertEqual(self.store.calls, [])

    async def test_bad_arguments_have_zero_store_dispatches(self):
        with self.assertRaises(ToolError):
            await execute_tool("get_node", {"gid": 7}, self.store, self.run_id)
        self.assertEqual(self.store.calls, [])

    async def test_stale_request_has_zero_store_dispatches(self):
        with self.assertRaises(RequestError):
            await execute_tool("get_node", {"gid": "0007"}, self.store, "old-run")
        self.assertEqual(self.store.calls, [])

    async def test_node_id_preserves_leading_zero_and_result_is_copied(self):
        result = await execute_tool("get_node", {"gid": "0007"}, self.store, self.run_id)
        self.assertEqual(result["gid"], "0007")
        result["gid"] = "changed"
        self.assertEqual(self.store.nodes["0007"]["gid"], "0007")

    async def test_requested_missing_node_is_tool_error(self):
        with self.assertRaises(ToolError) as caught:
            await execute_tool("get_node", {"gid": "missing"}, self.store, self.run_id)
        self.assertEqual(caught.exception.code, "target_not_found")

    async def test_missing_target_prevents_dependent_dispatch(self):
        for name in ["get_neighbors", "check_concentration"]:
            self.store.calls.clear()
            with self.subTest(name=name), self.assertRaises(ToolError):
                await execute_tool(name, {"gid": "missing"}, self.store, self.run_id)
            self.assertEqual([call[0] for call in self.store.calls], ["get_node"])

    async def test_concentration_is_delegated_unchanged(self):
        result = await execute_tool("check_concentration", {"gid": "0007"}, self.store, self.run_id)
        self.assertEqual(result, self.store.concentration)
        self.assertEqual(result["top_receiver_share"], 0.7)
        self.assertEqual([call[0] for call in self.store.calls], ["get_node", "check_concentration"])

    async def test_cluster_result(self):
        result = await execute_tool("get_cluster", {"cluster_id": "cluster-1"}, self.store, self.run_id)
        self.assertEqual(result, self.store.cluster)

    async def test_neighbors_use_defaults_and_validate_node_first(self):
        result = await execute_tool("get_neighbors", {"gid": "0007"}, self.store, self.run_id)
        self.assertEqual(result, self.store.graph)
        self.assertEqual(self.store.calls, [
            ("get_node", {"gid": "0007"}),
            ("get_neighbors", {"gid": "0007", "radius": 1, "limit": 50}),
        ])

    async def test_substituted_node_and_concentration_ids_are_rejected(self):
        self.store.nodes["0007"] = {"gid": "7"}
        with self.assertRaises(ToolError):
            await execute_tool("get_node", {"gid": "0007"}, self.store, self.run_id)
        self.store.nodes["0007"] = {"gid": "0007"}
        self.store.concentration["gid"] = "7"
        with self.assertRaises(ToolError):
            await execute_tool("check_concentration", {"gid": "0007"}, self.store, self.run_id)

    async def test_substituted_cluster_id_is_rejected(self):
        async def wrong_cluster(cluster_id):
            result = copy.deepcopy(self.store.cluster)
            result["cluster_id"] = "other"
            return result

        self.store.get_cluster = wrong_cluster
        with self.assertRaises(ToolError):
            await execute_tool("get_cluster", {"cluster_id": "cluster-1"}, self.store, self.run_id)

    async def test_graph_from_other_run_is_request_error(self):
        self.store.graph["run_id"] = "previous-run"
        with self.assertRaises(RequestError) as caught:
            await execute_tool("get_neighbors", {"gid": "0007"}, self.store, self.run_id)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_malformed_graph_is_rejected(self):
        valid_graph = copy.deepcopy(self.store.graph)
        patches = [
            {"contract_version": "2.0"},
            {"nodes": [{"gid": "7"}], "shown_nodes": 1, "total_nodes": 1, "edges": []},
            {"nodes": [{"gid": "0007"}, {"gid": "0007"}]},
            {"edges": [{"source": "0007", "target": "missing", "amount_kzt": "1.00", "tx_count": 1}]},
            {"edges": [{"source": "0007", "target": "7", "amount_kzt": "NaN", "tx_count": 1}]},
            {"total_nodes": 3},
            {"shown_nodes": 1},
            {"truncated": 0},
        ]
        for patch in patches:
            self.store.graph = {**copy.deepcopy(valid_graph), **patch}
            with self.subTest(patch=patch), self.assertRaises(ToolError):
                await execute_tool("get_neighbors", {"gid": "0007"}, self.store, self.run_id)

    async def test_run_change_during_store_call_is_rejected(self):
        def change_run(gid):
            self.store.run_id = "new-run"
            return {"gid": gid}

        self.store.get_node = change_run
        with self.assertRaises(RequestError) as caught:
            await execute_tool("get_node", {"gid": "0007"}, self.store, self.run_id)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_async_store_is_supported(self):
        async def read(gid):
            return {"gid": gid}

        self.store.get_node = read
        result = await execute_tool("get_node", {"gid": "0007"}, self.store, self.run_id)
        self.assertEqual(result, {"gid": "0007"})

    async def test_sync_store_read_does_not_block_deadline(self):
        def slow_read(gid):
            time.sleep(0.08)
            return {"gid": gid}

        self.store.get_node = slow_read
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(execute_tool("get_node", {"gid": "0007"}, self.store, self.run_id), timeout=0.01)

    async def test_store_exception_does_not_leak_details(self):
        def broken_read(gid):
            raise RuntimeError("secret-provider-key=never-show-this")

        self.store.get_node = broken_read
        with self.assertRaises(ToolError) as caught:
            await execute_tool("get_node", {"gid": "0007"}, self.store, self.run_id)
        self.assertNotIn("never-show-this", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
