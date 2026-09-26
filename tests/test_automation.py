from __future__ import annotations

from pathlib import Path
from unittest import TestCase, mock

from kaban.storage import content_hash, write_json


class AutomationStateTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.root = Path(self.tmp)
        self.generated = self.root / "generated"
        self.generated.mkdir()
        self.generated_patch = mock.patch("projects.caelus.storage.GENERATED", self.generated)
        self.generated_patch.start()
        self.addCleanup(self.generated_patch.stop)

    def _paths(self, day="2099-01-15", language="ru"):
        base = self.generated / day / language
        base.mkdir(parents=True, exist_ok=True)
        return base

    def test_derive_state_pending_generation_without_dataset(self):
        from projects.caelus import automation
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch("projects.caelus.automation_store.GENERATED", self.generated):
            state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "pending_generation")
        self.assertIsNone(state.content_hash)

    def test_derive_state_generation_failed_from_last_failed_generate(self):
        from projects.caelus import automation, automation_store
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch.object(automation_store, "GENERATED", self.generated):
            automation_store.write_run("2099-01-15", "ru", {
                "project_id": "caelus",
                "date": "2099-01-15",
                "language": "ru",
                "last_operation": "generate",
                "last_result": "failed",
                "attempt": 1,
                "started_at": "2099-01-14T20:00:00+00:00",
                "finished_at": "2099-01-14T20:00:01+00:00",
                "last_error": {"type": "RuntimeError", "message": "provider failed"},
                "content_hash": None,
                "history": [],
            })
            state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "generation_failed")
        self.assertEqual(state.attempt, 1)
        self.assertEqual(state.last_error["type"], "RuntimeError")

    def test_derive_state_review_ready_invalid_and_published_precedence(self):
        from projects.caelus import automation, automation_store
        base = self._paths()
        payload = {"date": "15 января 2099", "iso_date": "2099-01-15", "language": "ru", "signs": {}}
        write_json(base / "content.json", payload)
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch.object(automation_store, "GENERATED", self.generated):
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "review_required")
            write_json(base / "status.json", {"state": "approved", "content_hash": "wrong"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "approval_invalid")
            write_json(base / "status.json", {"state": "approved", "content_hash": content_hash(payload)})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "ready_to_publish")
            write_json(base / "publication.json", {"state": "partially_published"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "publishing")
            write_json(base / "publication.json", {"state": "failed"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "publish_failed")
            write_json(base / "publication.json", {"state": "published"})
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "published")

    def test_corrupt_optional_run_json_does_not_break_business_state(self):
        from projects.caelus import automation, automation_store
        base = self._paths()
        write_json(base / "content.json", {"iso_date": "2099-01-15", "language": "ru", "signs": {}})
        with mock.patch.object(automation, "GENERATED", self.generated), \
             mock.patch.object(automation_store, "GENERATED", self.generated):
            path = automation_store.run_path("2099-01-15", "ru")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")
            state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "review_required")
        self.assertIsNone(state.last_operation)


class AutomationStoreTests(TestCase):
    def test_sanitize_exception_removes_api_keys_tokens_and_credential_urls(self):
        from projects.caelus.automation_store import sanitize_exception
        exc = RuntimeError(
            "OPENAI_API_KEY=sk-secret123 TELEGRAM_BOT_TOKEN=123456:ABCDEF "
            "https://api.telegram.org/bot123456:ABCDEF/sendMessage Bearer sk-othersecret"
        )
        safe = sanitize_exception(exc)
        self.assertEqual(safe["type"], "RuntimeError")
        self.assertNotIn("sk-secret123", safe["message"])
        self.assertNotIn("123456:ABCDEF", safe["message"])
        self.assertNotIn("sk-othersecret", safe["message"])
        self.assertIn("[REDACTED]", safe["message"])


class AutomationLockTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()

    def test_existing_lock_rejects_second_invocation_and_names_lock_file(self):
        from projects.caelus import automation_store
        with mock.patch.object(automation_store, "GENERATED", self.generated):
            lock = automation_store.automation_dir("2099-01-15", "ru") / "generate.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"pid":999,"acquired_at":"2099-01-14T20:00:00+00:00"}', encoding="utf-8")
            with self.assertRaises(automation_store.AutomationBusyError) as ctx:
                with automation_store.operation_lock("2099-01-15", "ru", "generate"):
                    pass
        self.assertIn(str(lock), str(ctx.exception))
        self.assertTrue(lock.exists())

    def test_operation_lock_is_removed_after_exception(self):
        from projects.caelus import automation_store
        with mock.patch.object(automation_store, "GENERATED", self.generated):
            lock = automation_store.automation_dir("2099-01-15", "ru") / "publish.lock"
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with automation_store.operation_lock("2099-01-15", "ru", "publish"):
                    self.assertTrue(lock.exists())
                    raise RuntimeError("boom")
            self.assertFalse(lock.exists())

    def test_operation_lock_does_not_delete_replaced_lock_file(self):
        from projects.caelus import automation_store
        with mock.patch.object(automation_store, "GENERATED", self.generated):
            with automation_store.operation_lock("2099-01-15", "ru", "generate") as lock:
                lock.write_text('{"pid":4242,"acquired_at":"replacement"}', encoding="utf-8")
            self.assertTrue(lock.exists())

    def test_next_attempt_counts_attempts_per_operation(self):
        from projects.caelus.automation_store import next_attempt
        run = {
            "last_operation": "publish",
            "attempt": 2,
            "history": [
                {"operation": "generate", "attempt": 1},
                {"operation": "publish", "attempt": 1},
            ],
        }
        self.assertEqual(next_attempt(run, "generate"), 2)
        self.assertEqual(next_attempt(run, "publish"), 3)


class AutomationGenerationJobTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()
        for target in (
            "projects.caelus.automation_store.GENERATED",
            "projects.caelus.automation.GENERATED",
            "projects.caelus.storage.GENERATED",
        ):
            patcher = mock.patch(target, self.generated)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_existing_content_skips_generation_without_force(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True)
        original = {"iso_date": "2099-01-15", "language": "ru", "signs": {"keep": "me"}}
        write_json(base / "content.json", original)
        with mock.patch("projects.caelus.automation.generate_bundle") as generate:
            state = automation.run_generation_job("2099-01-15", "ru", mode="mock")
        generate.assert_not_called()
        self.assertEqual(state.last_result, "skipped")
        self.assertEqual(__import__("json").loads((base / "content.json").read_text(encoding="utf-8")), original)

    def test_successful_generation_records_success_and_review_required(self):
        from projects.caelus import automation
        payload = {"iso_date": "2099-01-15", "language": "ru", "signs": {}}
        def fake_generate(day, language, mode, model=None):
            base = self.generated / day / language
            base.mkdir(parents=True, exist_ok=True)
            write_json(base / "content.json", payload)
            write_json(base / "status.json", {"state": "draft"})
            return payload
        with mock.patch("projects.caelus.automation.generate_bundle", side_effect=fake_generate):
            state = automation.run_generation_job("2099-01-15", "ru", mode="mock")
        self.assertEqual(state.state, "review_required")
        self.assertEqual(state.last_result, "success")
        self.assertEqual(state.attempt, 1)

    def test_failed_new_generation_removes_partial_dataset_and_records_failure(self):
        from projects.caelus import automation
        def broken(day, language, mode, model=None):
            base = self.generated / day / language
            (base / "cards").mkdir(parents=True, exist_ok=True)
            write_json(base / "content.json", {"partial": True})
            (base / "cards" / "partial.png").write_bytes(b"partial")
            raise RuntimeError("generation exploded")
        with mock.patch("projects.caelus.automation.generate_bundle", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "generation exploded"):
                automation.run_generation_job("2099-01-15", "ru", mode="mock")
        self.assertFalse((self.generated / "2099-01-15" / "ru").exists())
        state = automation.derive_state("2099-01-15", "ru")
        self.assertEqual(state.state, "generation_failed")
        self.assertEqual(state.last_result, "failed")

    def test_backup_failure_never_deletes_existing_dataset(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True)
        write_json(base / "content.json", {"stable": True})
        before = (base / "content.json").read_bytes()
        with mock.patch("projects.caelus.automation.shutil.copytree", side_effect=OSError("backup failed")), \
             mock.patch("projects.caelus.automation.generate_bundle") as generate:
            with self.assertRaisesRegex(OSError, "backup failed"):
                automation.run_generation_job("2099-01-15", "ru", mode="mock", force=True)
        generate.assert_not_called()
        self.assertTrue(base.is_dir())
        self.assertEqual((base / "content.json").read_bytes(), before)

    def test_failed_forced_generation_restores_previous_dataset_exactly(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        (base / "cards").mkdir(parents=True)
        (base / "telegram").mkdir()
        write_json(base / "content.json", {"stable": True})
        write_json(base / "status.json", {"state": "approved", "content_hash": "oldhash"})
        (base / "cards" / "caelus_aries.png").write_bytes(b"OLDPNG")
        (base / "telegram" / "01.md").write_bytes(b"OLDTEXT")
        before = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
        def broken(day, language, mode, model=None):
            write_json(base / "content.json", {"new": "partial"})
            (base / "cards" / "caelus_aries.png").write_bytes(b"BROKEN")
            raise RuntimeError("forced generation failed")
        with mock.patch("projects.caelus.automation.generate_bundle", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "forced generation failed"):
                automation.run_generation_job("2099-01-15", "ru", mode="mock", force=True)
        after = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_failed_generation_restores_preexisting_non_dataset_directory(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True)
        (base / "operator-note.txt").write_text("keep", encoding="utf-8")
        def broken(day, language, mode, model=None):
            write_json(base / "content.json", {"partial": True})
            raise RuntimeError("boom")
        with mock.patch("projects.caelus.automation.generate_bundle", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                automation.run_generation_job("2099-01-15", "ru", mode="mock")
        self.assertEqual((base / "operator-note.txt").read_text(encoding="utf-8"), "keep")
        self.assertFalse((base / "content.json").exists())


class AutomationGenerationIntegrationTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()
        for target in (
            "projects.caelus.automation_store.GENERATED",
            "projects.caelus.automation.GENERATED",
            "projects.caelus.storage.GENERATED",
            "projects.caelus.workflow.GENERATED",
        ):
            patcher = mock.patch(target, self.generated)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_real_mock_generation_creates_complete_bundle(self):
        from projects.caelus import automation
        state = automation.run_generation_job("2099-01-16", "ru", mode="mock")
        base = self.generated / "2099-01-16" / "ru"
        self.assertEqual(state.state, "review_required")
        self.assertTrue((base / "content.json").is_file())
        self.assertEqual(len(list((base / "cards").glob("caelus_*.png"))), 12)
        self.assertTrue((base / "telegram").is_dir())


class AutomationPublicationJobTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()
        for target in (
            "projects.caelus.automation_store.GENERATED",
            "projects.caelus.automation.GENERATED",
            "projects.caelus.storage.GENERATED",
        ):
            patcher = mock.patch(target, self.generated)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _approved(self):
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True, exist_ok=True)
        payload = {"iso_date": "2099-01-15", "language": "ru", "signs": {}}
        write_json(base / "content.json", payload)
        write_json(base / "status.json", {"state": "approved", "content_hash": content_hash(payload)})
        return base, payload

    def test_unapproved_publication_is_refused_without_calling_publisher(self):
        from projects.caelus import automation
        base = self.generated / "2099-01-15" / "ru"
        base.mkdir(parents=True)
        write_json(base / "content.json", {"iso_date": "2099-01-15", "language": "ru", "signs": {}})
        write_json(base / "status.json", {"state": "draft"})
        with mock.patch("projects.caelus.automation.run_telegram_publisher") as publisher:
            with self.assertRaises(automation.AutomationPreconditionError):
                automation.run_publication_job("2099-01-15", "ru")
        publisher.assert_not_called()
        self.assertEqual(automation.derive_state("2099-01-15", "ru").last_result, "skipped")

    def test_already_published_is_skipped_without_second_send(self):
        from projects.caelus import automation
        base, _ = self._approved()
        write_json(base / "publication.json", {"state": "published"})
        with mock.patch("projects.caelus.automation.run_telegram_publisher") as publisher:
            state = automation.run_publication_job("2099-01-15", "ru")
        publisher.assert_not_called()
        self.assertEqual(state.state, "published")
        self.assertEqual(state.last_result, "skipped")

    def test_ready_dataset_calls_existing_publisher_and_records_success(self):
        from projects.caelus import automation
        base, payload = self._approved()
        def fake_publisher(day, language, dry_run=False, force=False):
            write_json(base / "publication.json", {"state": "published", "content_hash": content_hash(payload)})
            return 0, "published"
        with mock.patch("projects.caelus.automation.run_telegram_publisher", side_effect=fake_publisher):
            state = automation.run_publication_job("2099-01-15", "ru")
        self.assertEqual(state.state, "published")
        self.assertEqual(state.last_result, "success")

    def test_partial_failure_remains_resumable_and_second_job_delegates_resume(self):
        from projects.caelus import automation
        base, payload = self._approved()
        calls = []
        def fake_publisher(day, language, dry_run=False, force=False):
            calls.append((day, language, dry_run, force))
            if len(calls) == 1:
                write_json(base / "publication.json", {
                    "state": "partially_published",
                    "content_hash": content_hash(payload),
                    "media": [{"group": 1, "message_ids": [101]}],
                    "text": [],
                })
                return 1, "network failed after album 1"
            write_json(base / "publication.json", {
                "state": "published",
                "content_hash": content_hash(payload),
                "media": [{"group": 1, "message_ids": [101]}, {"group": 2, "message_ids": [102]}],
                "text": [{"batch": 1, "message_id": 201}],
            })
            return 0, "resumed"
        with mock.patch("projects.caelus.automation.run_telegram_publisher", side_effect=fake_publisher):
            with self.assertRaisesRegex(RuntimeError, "network failed"):
                automation.run_publication_job("2099-01-15", "ru")
            self.assertEqual(automation.derive_state("2099-01-15", "ru").state, "publishing")
            state = automation.run_publication_job("2099-01-15", "ru")
        self.assertEqual(state.state, "published")
        self.assertEqual(len(calls), 2)

    def test_publisher_wrapper_delegates_dry_run_and_force_flags(self):
        from projects.caelus.publication import run_telegram_publisher
        fake = mock.Mock(returncode=0, stdout="ok")
        with mock.patch("projects.caelus.publication.subprocess.run", return_value=fake) as run:
            run_telegram_publisher("2099-01-15", "ru", dry_run=True, force=True)
        cmd = run.call_args.args[0]
        self.assertIn("--dry-run", cmd)
        self.assertIn("--force", cmd)


class AutomationAdminUiTests(TestCase):
    def test_automation_status_html_shows_state_attempt_and_safe_error(self):
        import admin_app
        from projects.caelus.automation import AutomationState
        state = AutomationState(
            state="generation_failed",
            date="2099-01-15",
            language="ru",
            content_hash=None,
            last_operation="generate",
            last_result="failed",
            attempt=2,
            last_error={"type": "AIProviderError", "message": "provider unavailable"},
            updated_at="2099-01-14T20:00:01+00:00",
        )
        with mock.patch("admin_app.derive_state", return_value=state):
            html = admin_app.automation_status_html("2099-01-15", "ru")
        self.assertIn("GENERATION FAILED", html)
        self.assertIn("generate", html)
        self.assertIn("Попытка: 2", html)
        self.assertIn("provider unavailable", html)

    def test_history_includes_automation_only_failed_generation(self):
        import admin_app
        from projects.caelus.automation import AutomationState
        failed = AutomationState(
            state="generation_failed", date="2099-01-15", language="ru",
            content_hash=None, last_operation="generate", last_result="failed",
            attempt=1, last_error={"type": "RuntimeError", "message": "failed"},
            updated_at="2099-01-14T20:00:01+00:00",
        )
        with mock.patch("admin_app.iter_automation_targets", return_value=[("2099-01-15", "ru")]), \
             mock.patch("admin_app.derive_state", return_value=failed), \
             mock.patch("admin_app.GENERATED", Path("/path/that/does/not/exist")):
            html = admin_app.history_html().decode("utf-8")
        self.assertIn("2099-01-15", html)
        self.assertIn("GENERATION FAILED", html)
        self.assertNotIn("_AUTOMATION", html)

    def test_admin_routes_use_automation_jobs_while_manual_cli_stays_direct(self):
        source = Path("admin_app.py").read_text(encoding="utf-8")
        run_daily = Path("run_daily.py").read_text(encoding="utf-8")
        self.assertIn("run_generation_job", source)
        self.assertIn("run_publication_job", source)
        self.assertIn("generate_bundle", run_daily)


class AutomationCliTests(TestCase):
    def test_root_automation_entrypoint_is_thin(self):
        source = Path("automation.py").read_text(encoding="utf-8")
        self.assertIn("projects.caelus.automation_cli", source)
        self.assertNotIn("def run_generation_job", source)
        self.assertNotIn("def run_publication_job", source)

    def test_status_cli_prints_machine_readable_json(self):
        from projects.caelus import automation_cli
        from projects.caelus.automation import AutomationState
        state = AutomationState(
            state="review_required", date="2099-01-15", language="ru",
            content_hash="abc", last_operation="generate", last_result="success",
            attempt=1, last_error=None, updated_at="2099-01-14T20:00:00+00:00",
        )
        with mock.patch("projects.caelus.automation_cli.derive_state", return_value=state), \
             mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            code = automation_cli.main(["status", "--date", "2099-01-15", "--language", "ru"])
        self.assertEqual(code, 0)
        payload = __import__("json").loads(out.getvalue())
        self.assertEqual(payload["state"], "review_required")
        self.assertEqual(payload["last_result"], "success")

    def test_generate_mock_delegates_to_generation_job(self):
        from projects.caelus import automation_cli
        from projects.caelus.automation import AutomationState
        state = AutomationState("review_required", "2099-01-15", "ru", None, "generate", "success", 1, None, None)
        with mock.patch("projects.caelus.automation_cli.run_generation_job", return_value=state) as run, \
             mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            automation_cli.main(["generate", "--date", "2099-01-15", "--language", "ru", "--mock"])
        run.assert_called_once_with("2099-01-15", "ru", mode="mock", model=None, force=False)

    def test_publish_dry_run_delegates_to_publication_job(self):
        from projects.caelus import automation_cli
        from projects.caelus.automation import AutomationState
        state = AutomationState("ready_to_publish", "2099-01-15", "ru", None, "publish", "success", 1, None, None)
        with mock.patch("projects.caelus.automation_cli.run_publication_job", return_value=state) as run, \
             mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            automation_cli.main(["publish", "--date", "2099-01-15", "--language", "ru", "--dry-run"])
        run.assert_called_once_with("2099-01-15", "ru", dry_run=True, force=False)


class AutomationEndToEndTests(TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.generated = Path(self.tmp) / "generated"
        self.generated.mkdir()
        for target in (
            "projects.caelus.automation_store.GENERATED",
            "projects.caelus.automation.GENERATED",
            "projects.caelus.storage.GENERATED",
            "projects.caelus.workflow.GENERATED",
        ):
            patcher = mock.patch(target, self.generated)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_mock_state_flow_reaches_published_with_two_successful_attempts(self):
        from projects.caelus import automation, automation_store, workflow
        day = "2099-02-01"
        self.assertEqual(automation.derive_state(day, "ru").state, "pending_generation")
        self.assertEqual(automation.run_generation_job(day, "ru", mode="mock").state, "review_required")
        workflow.approve(day, "ru", {})
        self.assertEqual(automation.derive_state(day, "ru").state, "ready_to_publish")
        base = self.generated / day / "ru"
        def fake_publisher(_day, _language, dry_run=False, force=False):
            payload = __import__("json").loads((base / "content.json").read_text(encoding="utf-8"))
            write_json(base / "publication.json", {"state": "published", "content_hash": content_hash(payload)})
            return 0, "published"
        with mock.patch("projects.caelus.automation.run_telegram_publisher", side_effect=fake_publisher):
            self.assertEqual(automation.run_publication_job(day, "ru").state, "published")
        history = automation_store.load_run(day, "ru")["history"]
        successes = [(x["operation"], x["result"]) for x in history if x.get("result") == "success"]
        self.assertEqual(successes, [("generate", "success"), ("publish", "success")])

    def test_manual_cli_paths_are_not_redirected_through_automation(self):
        run_daily = Path("run_daily.py").read_text(encoding="utf-8")
        publish = Path("publish_telegram.py").read_text(encoding="utf-8")
        self.assertIn("projects.caelus.workflow import generate_bundle", run_daily)
        self.assertNotIn("run_generation_job", run_daily)
        self.assertIn("projects.caelus.publication import *", publish)
        self.assertNotIn("run_publication_job", publish)
        for path in Path("kaban").rglob("*.py"):
            self.assertNotIn("projects.caelus.automation", path.read_text(encoding="utf-8"))
