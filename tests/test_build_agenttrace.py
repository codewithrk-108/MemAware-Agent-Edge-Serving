"""The AgentTrace converter must send the agent context, not just the short task."""

import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from workloads.build_agenttrace import JsonTokenizer, TokenizeClient, convert_row, convert_source

TOKENIZER_JSON = Path(__file__).resolve().parents[1] / "data/source/Qwen3-0.6B-tokenizer.json"


def byte_tokens(text):
    return list(text.encode("utf-8"))


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "task_id": "agenttrace-mbpp-11-qwen3-0.6b",
            "source_task_key": "agenttrace-mbpp-11",
            "cls": "mbpp",
            "split": "dev",
            "source_model": "Qwen/Qwen3-0.6B",
            "initial_prompt": "Write a Python function.",
            "steps": [
                {"n_gen": 7, "tool": {"name": "python_interpreter", "result_text": "Output: 3", "observed_delay_s": 0.025}},
                {"n_gen": 5, "tool": None},
            ],
        }

    def test_first_input_has_system_and_later_input_has_tool_result(self):
        task = convert_row(self.row, byte_tokens, "PINNED AGENT INSTRUCTIONS", max_context=4096)
        self.assertEqual(set(task), {"task_id", "cls", "source", "steps"})
        first, second = task["steps"]
        first_text = bytes(first["input"]).decode()
        second_text = bytes(second["input"]).decode()
        self.assertIn("<|im_start|>system\nPINNED AGENT INSTRUCTIONS<|im_end|>", first_text)
        self.assertIn("<|im_start|>user\nWrite a Python function.<|im_end|>", first_text)
        self.assertTrue(first_text.endswith("<|im_start|>assistant\n"))
        self.assertEqual(first["n_gen"], 7)
        self.assertEqual(first["tool_delay_s"], 0.025)
        self.assertIn("<|im_start|>tool\npython_interpreter: Output: 3<|im_end|>", second_text)
        self.assertTrue(second_text.endswith("<|im_start|>assistant\n"))
        self.assertEqual(second["n_gen"], 5)
        self.assertIsNone(second["tool_delay_s"])

    def test_context_limit_rejects_instead_of_shortening_output(self):
        with self.assertRaisesRegex(ValueError, "context_limit"):
            convert_row(self.row, byte_tokens, "PINNED AGENT INSTRUCTIONS", max_context=20)

    def test_source_filter_and_output_match_runner_schema(self):
        with TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "source.jsonl", Path(tmp) / "trace.jsonl"
            eval_row = {**self.row, "task_id": "eval-run", "split": "eval"}
            src.write_text("".join(json.dumps(r) + "\n" for r in (self.row, eval_row)))
            report = convert_source(src, out, byte_tokens, "PINNED AGENT INSTRUCTIONS", split="dev", max_context=4096)
            tasks = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(report["written"], 1)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["task_id"], self.row["task_id"])
            self.assertEqual(set(tasks[0]["steps"][0]), {"input", "n_gen", "tool_delay_s"})

    def test_http_tokenizer_requests_special_token_parsing(self):
        bodies = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                bodies.append((self.path, body))
                data = json.dumps({"tokens": [13, 17]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = TokenizeClient(f"http://127.0.0.1:{server.server_port}").tokenize("<|im_start|>system\nx")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(result, [13, 17])
        self.assertEqual(bodies, [("/tokenize", {"content": "<|im_start|>system\nx", "parse_special": True})])

    @unittest.skipUnless(TOKENIZER_JSON.exists(), "download the tokenizer first (see data/README.md)")
    def test_offline_qwen_tokenizer_preserves_chat_markers(self):
        tokenizer_json = TOKENIZER_JSON
        tokenizer = JsonTokenizer(tokenizer_json)
        self.assertEqual(tokenizer.tokenize("<|im_start|>user\nHi<|im_end|>\n<|im_start|>assistant\n"),
                         [151644, 872, 198, 13048, 151645, 198, 151644, 77091, 198])


if __name__ == "__main__":
    unittest.main()
