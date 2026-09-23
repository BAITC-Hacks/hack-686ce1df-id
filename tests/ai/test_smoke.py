import contextlib
import io
import json
import unittest
from unittest.mock import patch

from backend.app.ai.smoke import main


class SmokeTests(unittest.TestCase):
    def test_no_key_is_local_error_without_network(self):
        for flag in ("--list-models", "--smoke"):
            output = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(output):
                code = main([flag])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue()), {"status": "error", "reason": "missing_api_key"})

    def test_config_error_does_not_echo_value(self):
        output = io.StringIO()
        with patch.dict("os.environ", {"AI_TIMEOUT_SECONDS": "private-invalid-value"}), contextlib.redirect_stdout(output):
            code = main(["--smoke"])
        self.assertEqual(code, 1)
        self.assertNotIn("private-invalid-value", output.getvalue())
