from pathlib import Path
from uuid import uuid4
import pytest


def test_local_sent_step_is_skipped_without_external_call(tmp_path: Path):
    from kaban.cloud.publication import LocalPublicationCheckpointClient
    from kaban.storage import write_json
    base=tmp_path/"day"; base.mkdir()
    write_json(base/"publication.json", {"media":[{"group":1,"message_ids":[10,11]}],"text":[]})
    client=LocalPublicationCheckpointClient(base/"publication.json")
    decision=client.begin_step(uuid4(),"media:1","fingerprint")
    assert decision.action=="skip"
    assert decision.state=="sent"


def test_local_recovered_sending_becomes_unknown_and_never_resends(tmp_path: Path):
    from kaban.cloud.publication import LocalPublicationCheckpointClient
    from kaban.storage import write_json, load_json
    base=tmp_path/"day"; base.mkdir()
    write_json(base/"publication.json", {"media":[],"text":[],"checkpoints":{"media:1":{"state":"sending","request_fingerprint":"fp"}}})
    client=LocalPublicationCheckpointClient(base/"publication.json")
    decision=client.begin_step(uuid4(),"media:1","fp")
    assert decision.action=="manual"
    assert decision.state=="unknown_delivery"
    assert load_json(base/"publication.json")["checkpoints"]["media:1"]["state"]=="unknown_delivery"


def test_ambiguous_error_marks_unknown_and_is_manual(tmp_path: Path):
    from kaban.cloud.publication import LocalPublicationCheckpointClient, run_checkpointed_step, ManualReconciliationRequired
    from kaban.publishing.telegram import AmbiguousTelegramError
    client=LocalPublicationCheckpointClient(tmp_path/"publication.json")
    called=0
    def send():
        nonlocal called; called+=1
        raise AmbiguousTelegramError("timeout after request began")
    with pytest.raises(ManualReconciliationRequired):
        run_checkpointed_step(client,uuid4(),"text:1","fp",send,external_ids=lambda result:{"message_id":result["message_id"]})
    assert called==1
    decision=client.begin_step(uuid4(),"text:1","fp")
    assert decision.action=="manual"


class FakeCloudStore:
    def __init__(self): self.state="pending"; self.stale=False
    def transition_publication_step(self, **kwargs):
        from kaban.cloud.contracts import LeaseConflict
        if self.stale and kwargs["to_state"]=="sent": raise LeaseConflict("stale fence")
        if self.state==kwargs["from_state"] or (self.state=="pending" and kwargs["from_state"]=="pending"):
            self.state=kwargs["to_state"]
        return {"state":self.state,"request_fingerprint":kwargs["request_fingerprint"],"external_ids":kwargs.get("external_ids")}


def test_cloud_stale_fence_rejects_mark_sent_and_stops_old_runner():
    from kaban.cloud.publication import CloudPublicationCheckpointClient
    from kaban.cloud.contracts import LeaseConflict
    store=FakeCloudStore(); run_id=uuid4(); execution_id=uuid4()
    client=CloudPublicationCheckpointClient(store,project_id="caelus",publication_run_id=run_id,execution_id=execution_id,owner="runner-a",fence_token=1)
    assert client.begin_step(run_id,"media:1","fp").action=="send"
    store.stale=True
    with pytest.raises(LeaseConflict):
        client.mark_sent(run_id,"media:1",{"message_ids":[1,2]})
    assert store.state=="sending"


def test_caelus_media_step_uses_checkpoint_and_skips_sent(tmp_path: Path):
    from kaban.cloud.publication import LocalPublicationCheckpointClient
    from kaban.storage import write_json
    from projects.caelus.publication import publish_media_step

    journal = tmp_path / "publication.json"
    write_json(journal, {"media": [{"group": 1, "message_ids": [101, 102]}], "text": []})
    checkpoints = LocalPublicationCheckpointClient(journal)

    class Telegram:
        calls = 0
        def send_media_group(self, chat_id, paths, caption=None):
            self.calls += 1
            return [{"message_id": 999}]

    telegram = Telegram()
    result = publish_media_step(
        checkpoints=checkpoints,
        publication_run_id=uuid4(),
        telegram=telegram,
        chat_id="-100123",
        group_index=1,
        paths=[tmp_path / "a.jpg"],
        caption="Daily",
        approved_hash="content-hash",
    )

    assert result is None
    assert telegram.calls == 0


def test_caelus_text_step_ambiguous_delivery_is_not_auto_retried(tmp_path: Path):
    from kaban.cloud.publication import LocalPublicationCheckpointClient, ManualReconciliationRequired
    from kaban.publishing.telegram import AmbiguousTelegramError
    from kaban.storage import load_json
    from projects.caelus.publication import publish_text_step

    journal = tmp_path / "publication.json"
    checkpoints = LocalPublicationCheckpointClient(journal)

    class Telegram:
        calls = 0
        def send_message(self, chat_id, text):
            self.calls += 1
            raise AmbiguousTelegramError("timeout after request began")

    telegram = Telegram()
    run_id = uuid4()
    with pytest.raises(ManualReconciliationRequired):
        publish_text_step(
            checkpoints=checkpoints,
            publication_run_id=run_id,
            telegram=telegram,
            chat_id="-100123",
            batch_index=1,
            text="hello",
            approved_hash="content-hash",
        )

    assert telegram.calls == 1
    assert load_json(journal)["checkpoints"]["text:1"]["state"] == "unknown_delivery"

    with pytest.raises(ManualReconciliationRequired):
        publish_text_step(
            checkpoints=checkpoints,
            publication_run_id=run_id,
            telegram=telegram,
            chat_id="-100123",
            batch_index=1,
            text="hello",
            approved_hash="content-hash",
        )
    assert telegram.calls == 1


def test_caelus_main_routes_all_telegram_side_effects_through_checkpoints():
    import inspect
    from projects.caelus import publication

    source = inspect.getsource(publication.main)
    assert "checkpoint_client_from_env(" in source
    assert "publish_media_step(" in source
    assert "publish_text_step(" in source
    assert "client.send_media_group(" not in source
    assert "client.send_message(" not in source


def test_publication_journal_update_preserves_checkpoint_state(tmp_path: Path):
    from kaban.storage import load_json, write_json
    from projects.caelus.publication import write_publication_journal

    path = tmp_path / "publication.json"
    write_json(path, {
        "state": "publishing",
        "checkpoints": {"text:1": {"state": "sent", "request_fingerprint": "fp"}},
    })

    write_publication_journal(path, {"state": "published", "media": [], "text": []})

    saved = load_json(path)
    assert saved["state"] == "published"
    assert saved["checkpoints"]["text:1"]["state"] == "sent"
