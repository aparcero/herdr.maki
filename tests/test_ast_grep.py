"""Exercise the real plugin inside Maki, with a scripted loopback model (no API calls)."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from package_fixture import install_package

SOURCE = (
    'void f() {\r\n'
    '  oldApi("café", nested(x));\r\n'
    '  oldApi(\r\n    first, second\r\n  );\r\n'
    '  // oldApi(comment_only);\r\n'
    '  const char* s = "oldApi(string_only)";\r\n'
    '}\r\n'
).encode()


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for binary in ("maki", "ast-grep"):
            if not shutil.which(binary):
                required = "maki 0.7.0+" if binary == "maki" else binary
                raise RuntimeError(f"just check requires {required} on PATH")
        if (Path.home() / ".maki").exists():
            raise RuntimeError("Legacy ~/.maki would override the isolated test configuration")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="maki-ast-grep-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.file = self.work / "sample 'quoted' & spaced.cpp"
        self.file.write_bytes(SOURCE)
        self.config = self.root / "config/maki"
        self.config.mkdir(parents=True)
        self.package = install_package(self.root)
        # Plugin permissions must come from the package, not the local config.
        (self.config / "plugin.toml").write_text('[permissions]\nrun = false\nenv = false\n')
        self.init = 'maki.setup({ always_yolo = true })\n'
        self.package_init = ""
        # Only tool discovery and loopback HTTP are needed; no user credentials,
        # provider overrides, shared MCP servers, user packages, or saved preferences.
        self.env = {key: value for key, value in os.environ.items() if key in (
            "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG",
        )}
        for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
            self.env[f"XDG_{kind}_HOME"] = str(self.root / kind.lower())
        if os.name == "nt":
            self.env["APPDATA"] = str(self.root / "config")
            self.env["LOCALAPPDATA"] = str(self.root / "data")
        self.env["AST_GREP_TEST_API_KEY"] = "local-test-placeholder"

    def args(self, **extra):
        return {"pattern": "oldApi($$$ARGS)", "language": "cpp", "paths": [str(self.file)], **extra}

    def run_tools(self, calls, *, harness=False, stdin=None):
        requests = self.requests = []
        if harness:
            shutil.copyfile(Path(__file__).with_name("ast_grep_harness.lua"), self.package / "plugin/ast_grep.lua")
        if self.package_init:
            with (self.package / "plugin/ast_grep.lua").open("a") as entry:
                entry.write(self.package_init)
        (self.config / "init.lua").write_text(self.init)

        class ScriptedModel(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                index = len(requests)
                requests.append(payload)
                if index < len(calls):
                    name, arguments = calls[index]
                    delta = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": f"call_{index}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"role": "assistant", "content": "Tests complete."}, "stop"
                chunk = {"id": f"test_{index}", "object": "chat.completion.chunk", "model": "fixture",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                end = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
                data = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedModel)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            (self.config / "providers.toml").write_text(
                '[fixture]\nprotocol = "openai"\n'
                f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
                'api_key_env = "AST_GREP_TEST_API_KEY"\ndiscover_models = false\n'
                '[[fixture.models]]\nid = "fixture"\ncontext_window = 1000000\nmax_output_tokens = 4096\n'
            )
            result = subprocess.run(
                ["maki", "-p", "--model", "fixture/fixture", "--max-turns", str(len(calls) + 2),
                 "--output-format", "json", "Run the scripted test tools."],
                cwd=self.work, env=self.env, stdin=stdin, capture_output=True, text=True, timeout=90,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-4000:])
        self.assertEqual(len(requests), len(calls) + 1, result.stdout[-4000:])
        outputs = [m["content"] for m in requests[-1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(outputs), len(calls), requests[-1])
        return outputs, requests[0].get("tools", [])

    def test_search_and_preview_leave_bytes_unchanged(self):
        results, tools = self.run_tools([
            ("ast_grep_search", self.args()),
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)")),
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)", dry_run=True)),
        ])
        self.assertIn("Found 2 matches", results[0])
        for result in results[1:]:
            self.assertIn("Preview: 2 matches", result)
            self.assertIn('replacement: newApi("café", nested(x))', result)
        self.assertNotIn("comment_only", "".join(results))
        self.assertNotIn("string_only", "".join(results))
        self.assertEqual(self.file.read_bytes(), SOURCE)
        schemas = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
        self.assertEqual(set(schemas["ast_grep_rewrite"]["required"]), {"pattern", "language", "rewrite"})
        self.assertFalse(schemas["ast_grep_search"]["additionalProperties"])
        tree = schemas["ast_grep_syntax_tree"]
        self.assertEqual(set(tree["required"]), {"code", "language"})
        self.assertEqual(tree["properties"]["format"]["enum"], ["pattern", "ast", "cst", "sexp"])
        system = json.dumps([m for m in self.requests[0]["messages"] if m["role"] == "system"])
        self.assertIn("rather than ast-grep in bash", system)
        self.assertIn("**ast_grep_syntax_tree**", system)

    def test_apply_captures_and_globs_then_search(self):
        skipped = self.work / "skip.cpp"
        skipped.write_bytes(SOURCE)
        results, _ = self.run_tools([
            ("ast_grep_rewrite", self.args(paths=["."], globs=["!skip.cpp"], rewrite="newApi($$$ARGS)", dry_run=False)),
            ("ast_grep_search", self.args()),
        ])
        self.assertIn("Applied 2 changes", results[0])
        self.assertIn("No matches found", results[1])
        changed = self.file.read_bytes()
        self.assertIn('newApi("café", nested(x))'.encode(), changed)
        self.assertIn(b"newApi(first, second)", changed)
        self.assertIn(b"// oldApi(comment_only);\r\n", changed)
        self.assertIn(b'"oldApi(string_only)";\r\n', changed)
        self.assertEqual(skipped.read_bytes(), SOURCE)

    def test_legacy_local_modules_stay_inactive(self):
        modules = self.config / "lua"
        modules.mkdir()
        legacy = 'error("former local extension must not load")\n'
        for name in ("ast_grep.lua", "zai_local.lua"):
            (modules / name).write_text(legacy)
        results, tools = self.run_tools([("ast_grep_search", self.args())])
        self.assertIn("Found 2 matches", results[0])
        names = [tool["function"]["name"] for tool in tools]
        for name in ("ast_grep_search", "ast_grep_rewrite", "ast_grep_syntax_tree"):
            self.assertEqual(names.count(name), 1)
        self.assertNotIn("herdr_link", names)
        for module in modules.iterdir():
            self.assertEqual(module.read_text(), legacy)

    def test_empty_and_shell_significant_replacements_are_literal(self):
        replacement = 'note("a\'b & c; $(echo nope)")'
        results, _ = self.run_tools([
            ("ast_grep_rewrite", self.args(rewrite=replacement, dry_run=False)),
            ("ast_grep_rewrite", self.args(pattern="note($$$ARGS)", rewrite="", dry_run=False)),
        ])
        for result in results:
            self.assertIn("Applied 2 changes", result)
        self.assertIn(b"  ;\r\n  ;\r\n", self.file.read_bytes())
        self.assertIn(b"oldApi(comment_only)", self.file.read_bytes())

    def test_path_and_language_errors_never_apply(self):
        results, _ = self.run_tools([
            ("ast_grep_search", self.args(paths=["missing.cpp"])),
            ("ast_grep_search", self.args(language="not-a-language")),
            ("ast_grep_rewrite", self.args(paths=[str(self.file), "missing.cpp"], rewrite="newApi($$$ARGS)", dry_run=False)),
        ])
        for result in results:
            self.assertIn("search/preview failed", result)
            self.assertNotIn("No matches found", result)
        self.assertEqual(self.file.read_bytes(), SOURCE)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses read-only mode bits")
    @unittest.skipIf(os.name == "nt", "Unix mode-bit regression")
    def test_read_only_apply_reports_failure(self):
        self.file.chmod(0o444)
        self.addCleanup(self.file.chmod, 0o644)
        results, _ = self.run_tools([
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)", dry_run=False)),
        ])
        self.assertIn("apply failed", results[0])
        self.assertIn("may have changed some files", results[0])
        self.assertEqual(self.file.read_bytes(), SOURCE)

    def test_missing_binary(self):
        self.env["ASTGREP_BIN"] = str(self.root / "missing-ast-grep")
        results, _ = self.run_tools([("ast_grep_search", self.args())])
        self.assertIn("Could not run ast-grep", results[0])

    def test_compact_results_have_an_explicit_limit(self):
        self.file.write_text("oldApi(x);\n" * 120)
        results, _ = self.run_tools([("ast_grep_search", self.args())])
        self.assertIn("Found 120 matches", results[0])
        self.assertIn("Showing 100 of 120", results[0])
        self.assertNotIn(":101:1", results[0])

    def test_invalid_arguments_are_rejected(self):
        # Maki 0.7.0 validates arguments before dispatch: wrong types are
        # rejected by the host and unknown keys are dropped, so the adapter's
        # slot check now fires only for schema-valid-but-empty values (empty
        # arrays, blank strings). Nothing invalid reaches ast-grep either way.
        invalid = [
            (self.args(rewrite="newApi($$$ARGS)", paths=[]), "paths must not be empty"),
            (self.args(rewrite="newApi($$$ARGS)", pattern="  "), "pattern must be a non-empty string"),
            (self.args(rewrite="newApi($$$ARGS)", globs=[""]), "globs must be an array of non-empty strings"),
            (self.args(rewrite="newApi($$$ARGS)", paths="."), "invalid parameter"),
            (self.args(rewrite="newApi($$$ARGS)", globs={}), "invalid parameter"),
            (self.args(rewrite=42), "invalid parameter"),
        ]
        results, _ = self.run_tools([("ast_grep_rewrite", args) for args, _ in invalid])
        for result, (_, expected) in zip(results, invalid):
            self.assertIn(expected, result)
        self.assertEqual(self.file.read_bytes(), SOURCE)

    def test_handler_revalidates_arguments_the_host_cannot_reject(self):
        # Wrong-typed and unknown keys survive host validation only when a
        # caller invokes the handler directly (another plugin, the task API),
        # so the body re-validates instead of trusting its input.
        invalid = [
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)", dry_run="false"), "dry_run must be a boolean"),
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)", extra=True), "Unknown argument"),
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)", paths="."), "paths must be an array"),
            ("ast_grep_rewrite", self.args(rewrite=42), "rewrite must be a string"),
            ("ast_grep_syntax_tree", {"code": "oldApi(x)", "language": "cpp", "format": "json"}, "format must be one of"),
        ]
        calls = [("ast_grep_test", {"request": json.dumps({"input": args, "tool": tool})})
                 for tool, args, _ in invalid]
        results, _ = self.run_tools(calls, harness=True)
        for raw, (_, _, expected) in zip(results, invalid):
            result = json.loads(raw)
            self.assertTrue(result["is_error"])
            self.assertIn(expected, result["output"])

    def test_leading_hyphen_pattern_and_rewrite_are_values(self):
        source = self.work / "negate.py"
        source.write_text("y = neg(x)\nz = -w\n")
        args = {"language": "python", "paths": [str(source)]}
        results, _ = self.run_tools([
            ("ast_grep_search", {**args, "pattern": "-$A"}),
            ("ast_grep_rewrite", {**args, "pattern": "neg($A)", "rewrite": "-$A"}),
        ])
        self.assertIn("Found 1 matches", results[0])
        self.assertIn("negate.py:2:5", results[0])
        self.assertIn("replacement: -x", results[1])

    def test_syntax_tree_reads_neither_files_nor_stdin(self):
        # The working directory and Maki's stdin both hold matching code. Either
        # one being searched prints matches on stdout, which the tool rejects.
        stdin = self.root / "stdin.cpp"
        stdin.write_bytes(SOURCE)
        with stdin.open("rb") as handle:
            results, _ = self.run_tools([
                ("ast_grep_syntax_tree", {"code": "oldApi($$$ARGS)", "language": "cpp"}),
                ("ast_grep_syntax_tree", {"code": "a(); b();", "language": "cpp", "format": "ast"}),
                ("ast_grep_syntax_tree", {"code": "a(); b();", "language": "cpp"}),
                ("ast_grep_syntax_tree", {"code": "-$A", "language": "python", "format": "pattern"}),
                ("ast_grep_syntax_tree", {"code": "oldApi(x)", "language": "not-a-language"}),
                ("ast_grep_syntax_tree", {"code": "oldApi(x)", "language": "cpp", "format": "json"}),
                ("ast_grep_syntax_tree", {"code": " ", "language": "cpp"}),
            ], stdin=handle)
        self.assertIn("Debug Pattern:", results[0])
        self.assertIn("MetaVar $$$ARGS", results[0])
        self.assertNotIn("failed", results[0])
        # A multi-statement snippet still shows its tree, then why it is not a pattern.
        self.assertIn("Debug AST:", results[1])
        self.assertEqual(results[1].count("expression_statement"), 2)
        self.assertIn("Multiple AST nodes", results[1])
        self.assertNotIn("failed", results[1])
        self.assertIn("syntax tree failed", results[2])
        self.assertIn("Multiple AST nodes", results[2])
        self.assertIn("MetaVar $A", results[3])
        self.assertIn("syntax tree failed", results[4])
        # Since 0.7.0 the host rejects the bad enum before the adapter sees it.
        self.assertIn("invalid parameter 'format'", results[5])
        self.assertIn("code must be a non-empty string", results[6])

    def test_setup_sets_timeout_and_rejects_invalid_options(self):
        self.package_init = 'require("ast_grep.init").setup({ timeout_secs = 5 })\n'
        invalid = [
            ({"timeout_secs": 0}, "positive integer"),
            ({"timeout_secs": 1.5}, "positive integer"),
            ({"timeout_secs": "5"}, "positive integer"),
            ({"retries": 1}, "unknown option retries"),
            ("5", "expects a table"),
        ]
        calls = [("ast_grep_test", {"request": json.dumps({"setup": value})}) for value, _ in invalid]
        # The harness's jobwait asserts the timeout it receives.
        calls.append(("ast_grep_test", {"request": json.dumps({
            "input": self.args(), "tool": "ast_grep_search", "timeout": 5000,
            "results": [{"stdout": "[]", "stderr": "", "exit_code": 1, "truncated": False}],
        })}))
        results, _ = self.run_tools(calls, harness=True)
        for raw, (_, expected) in zip(results, invalid):
            result = json.loads(raw)
            self.assertFalse(result["ok"])
            self.assertIn(expected, result["error"])
        self.assertIn("No matches found", json.loads(results[-1])["output"])

    def test_native_rendering_expands_and_restores(self):
        self.file.write_text("oldApi(x);\n" * 120)
        results, _ = self.run_tools([("ast_grep_test", {"request": json.dumps({
            "input": self.args(rewrite="newApi($$$ARGS)"), "render": True,
        })})], harness=True)
        result = json.loads(results[0])
        self.assertGreater(len(result["after"]), len(result["before"]))
        self.assertEqual(result["before"], result["restored"])
        self.assertIn(":120:1", result["state"]["text"])
        self.assertNotIn(":120:1", result["output"])
        self.assertIn("preview", json.dumps(result["header"]))
        self.assertIn("diff_new", json.dumps(result["after"]))

    def test_matches_disappearing_before_apply_are_a_noop(self):
        args = ["ast-grep", "run", "--pattern=oldApi($$$ARGS)", "--lang=cpp",
                "--rewrite=newApi($$$ARGS)", "--color", "never"]

        def run(flag):
            result = subprocess.run([*args, flag, "--", str(self.file)],
                                    capture_output=True, text=True, timeout=30)
            return {"stdout": result.stdout, "stderr": result.stderr,
                    "exit_code": result.returncode, "truncated": False}

        preview = run("--json=compact")
        self.assertEqual(preview["exit_code"], 0, preview)
        self.assertEqual(len(json.loads(preview["stdout"])), 2)
        # Capture the real CLI result when another writer removes the matches
        # between preflight and apply, then replay it through the Maki handler.
        external = b"void f() { externalEdit(x); }\n"
        self.file.write_bytes(external)
        applied = run("--update-all")
        self.assertEqual(applied, {"stdout": "", "stderr": "", "exit_code": 1, "truncated": False})
        results, _ = self.run_tools([("ast_grep_test", {"request": json.dumps({
            "input": self.args(rewrite="newApi($$$ARGS)", dry_run=False),
            "results": [preview, applied],
        })})], harness=True)
        result = json.loads(results[0])
        self.assertFalse(result["is_error"], result)
        self.assertIn("No matches remain", result["output"])
        self.assertNotIn("may have changed some files", result["output"])
        self.assertEqual(result["calls"], 2)
        self.assertEqual(self.file.read_bytes(), external)

    def test_diagnostics_timeout_truncation_and_apply_report(self):
        match = {"file": "fixture.cpp", "text": "oldApi(x)", "replacement": "newApi(x)",
                 "range": {"start": {"line": 0, "column": 0}}}

        def output(stdout="[]", stderr="", code=0, **extra):
            return {"stdout": stdout, "stderr": stderr, "exit_code": code, "truncated": False, **extra}

        preview = output(json.dumps([match]))
        cases = [
            ([output(stderr="path error", code=1)], "search/preview failed", False, False),
            ([output("not JSON")], "invalid JSON", False, False),
            ([output("{}")], "unexpected search result", False, False),
            ([output(json.dumps([match]), code=1)], "unexpected search result", False, False),
            ([output(json.dumps([{**match, "replacement": None}]))], "replacement text", False, False),
            ([output(truncated=True)], "output was truncated", False, False),
            ([{"timeout": True}], "timed out", False, False),
            ([preview, output(stderr="write failed", code=1)], "apply failed", True, False),
            ([preview, output("", stderr="write failed", code=1)], "apply failed", True, False),
            ([preview, output("unexpected output", code=1)], "apply failed", True, False),
            ([preview, output("", code=2)], "apply failed", True, False),
            ([preview, output("", code=1, truncated=True)], "output was truncated", True, False),
            ([preview, output("")], "Unexpected ast-grep apply report", True, False),
            ([preview, output("", stderr="Applied 1 changes\nwrite error")], "Unexpected ast-grep apply report", True, False),
            ([preview, output("", stderr="Applied 1 changes")], "Applied 1 changes", True, True),
        ]
        calls = [("ast_grep_test", {"request": json.dumps({
            "input": self.args(rewrite="newApi($$$ARGS)", dry_run=not apply), "results": responses,
        })}) for responses, _, apply, _ in cases]
        results, _ = self.run_tools(calls, harness=True)
        for raw, (responses, expected, apply, success) in zip(results, cases):
            result = json.loads(raw)
            self.assertIn(expected, result["output"])
            self.assertEqual(result["is_error"], not success, result)
            self.assertEqual(result["calls"], len(responses))
            if apply and not success:
                self.assertIn("may have changed some files", result["output"])
            if responses[0].get("timeout"):
                self.assertEqual(result["stops"], 1)
        self.assertEqual(self.file.read_bytes(), SOURCE)

    def test_output_overflow_stops_process(self):
        results, _ = self.run_tools([("ast_grep_test", {"request": json.dumps({
            "input": self.args(), "tool": "ast_grep_search", "overflow": True,
            "results": [{"stdout": "[]", "stderr": "", "exit_code": 0}],
        })})], harness=True)
        result = json.loads(results[0])
        self.assertTrue(result["is_error"])
        self.assertEqual(result["stops"], 1)
        self.assertIn("exceeded 16 MiB", result["output"])

    def test_real_ast_grep_output_overflow_prevents_apply(self):
        # Exercise Maki's real process callbacks, without the Lua fault harness.
        # JSON locations and captures make 50,000 small matches exceed 16 MiB.
        source = b"oldApi(x);\n" * 50000
        self.file.write_bytes(source)
        results, _ = self.run_tools([
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)", dry_run=False)),
            ("ast_grep_search", self.args(pattern="missingApi($$$ARGS)")),
        ])
        self.assertIn("ast-grep output exceeded 16 MiB", results[0])
        self.assertNotIn("Applied", results[0])
        self.assertIn("No matches found", results[1])
        self.assertEqual(self.file.read_bytes(), source)

    def test_real_job_api_callback_types_and_truncation_flag(self):
        # Check the host API contract itself: the overflow path exits before
        # reading result.truncated, so that test alone cannot pin this field.
        (self.config / "plugin.toml").write_text('[permissions]\nrun = true\nenv = false\n')
        self.init += '''
maki.api.register_tool({
  name = "ast_grep_job_contract", description = "Check the real process API.",
  permission = "run", schema = { type = "object", properties = {} },
  handler = function()
    local lines = { stdout = 0, stderr = 0 }
    local strings = true
    local function count(stream)
      return function(_, line)
        strings = strings and type(line) == "string"
        lines[stream] = lines[stream] + 1
      end
    end
    local id, err = maki.fn.jobstart({
      "ast-grep", "run", "-p", "oldApi($$$ARGS)", "-l", "cpp",
      "--json=compact", "--", ".", "missing.cpp",
    }, { on_stdout = count("stdout"), on_stderr = count("stderr") })
    assert(id, err)
    local result
    result, err = maki.fn.jobwait(id, 30000)
    assert(result, err)
    return assert(maki.json.encode({
      strings = strings, lines = lines, truncated = result.truncated,
    }))
  end,
})
'''
        results, _ = self.run_tools([("ast_grep_job_contract", {})])
        result = json.loads(results[0])
        self.assertTrue(result["strings"])
        self.assertGreater(result["lines"]["stdout"], 0)
        self.assertGreater(result["lines"]["stderr"], 0)
        self.assertIs(result["truncated"], False)

    def test_plan_mode_blocks_apply_before_starting_process(self):
        results, _ = self.run_tools([("ast_grep_test", {"request": json.dumps({
            "input": self.args(rewrite="newApi($$$ARGS)", dry_run=False), "plan": True, "results": [],
        })})], harness=True)
        result = json.loads(results[0])
        self.assertTrue(result["is_error"])
        self.assertEqual(result["calls"], 0)
        self.assertIn("plan mode", result["output"])

    def test_preview_is_unprompted_and_apply_respects_permissions(self):
        self.init = self.init.replace("always_yolo = true", "always_yolo = false")
        results, _ = self.run_tools([
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)")),
            ("ast_grep_rewrite", self.args(rewrite="newApi($$$ARGS)", dry_run=False)),
        ])
        self.assertIn("Preview: 2 matches", results[0])
        self.assertNotIn("Applied", results[1])
        self.assertRegex(results[1].lower(), "permission|denied|not allowed")
        self.assertEqual(self.file.read_bytes(), SOURCE)

    @unittest.skipIf(os.name == "nt", "Unix executable fixture")
    def test_cancellation_kills_apply_and_releases_serialization(self):
        fake = self.root / "slow-ast-grep"
        pid_file = self.root / "apply.pid"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import os, sys, time\n"
            "if '--update-all' in sys.argv:\n"
            f"    with open({str(pid_file)!r}, 'w') as f: f.write(str(os.getpid()))\n"
            "    time.sleep(60)\n"
            f"os.execv({shutil.which('ast-grep')!r}, ['ast-grep', *sys.argv[1:]])\n"
        )
        fake.chmod(0o700)
        self.env["ASTGREP_BIN"] = str(fake)
        results, _ = self.run_tools([
            ("ast_grep_test", {"request": json.dumps({
                "input": self.args(rewrite="newApi($$$ARGS)", dry_run=False), "deadline": 1,
            })}),
            ("ast_grep_search", self.args()),
        ], harness=True)
        self.assertIn("timeout", results[0])
        self.assertIn("may have changed some files", results[0])
        self.assertIn("Found 2 matches", results[1])
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)


if __name__ == "__main__":
    unittest.main()
