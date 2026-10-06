import importlib
import os
import sys
from datetime import datetime

import mongomock
import pytest


ALICE_ID = "a1" * 12
BOB_ID = "b2" * 12
CAROL_ID = "c3" * 12


@pytest.fixture
def app_module():
    os.environ["MONGO_DBNAME"] = "test_app"
    os.environ["MONGODB_URI"] = "mongodb://localhost:27017/test_app"
    os.environ["DISABLE_FORCE_HTTPS"] = "1"

    sys.modules.pop("run", None)
    import run as run_module
    importlib.reload(run_module)

    # Swap the lazily-initialised real client for an in-memory one
    mock_client = mongomock.MongoClient(serverSelectionTimeoutMS=100)
    run_module.mongo.cx = mock_client
    run_module.mongo.db = mock_client["test_app"]

    run_module.app.config["WTF_CSRF_ENABLED"] = False
    run_module.app.config["TESTING"] = True

    # No real SMTP during tests
    run_module.send_message_email = lambda **kwargs: None

    yield run_module


@pytest.fixture
def db(app_module):
    return app_module.mongo.db


def _make_user(app_module, user_id, email):
    from user import User

    user = User("Mr", "Test", email.split("@")[0].title(), email, id=user_id)
    data = user.dict()
    data["password"] = app_module.bc.generate_password_hash("pw12345").decode("utf-8")
    app_module.mongo.db.users.insert_one(data)
    return user


def _login(client, email):
    response = client.post(
        "/login",
        data={"email": email, "pass": "pw12345"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def _message_doc(mid, frm, to, deleted="__missing__", hidden="__missing__"):
    doc = {
        "id": mid,
        "title": mid,
        "body": "body of " + mid,
        "from_id": frm,
        "from_name": "sender",
        "to_name": "recipient",
        "timestamp": datetime.utcnow(),
        "date_string": "1 Jan 2026 00:00",
    }
    if to is not None:
        doc["to_id"] = to
    if deleted != "__missing__":
        doc["deleted"] = deleted
    if hidden != "__missing__":
        doc["hidden_for_sender"] = hidden
    return doc


def _note_doc(nid, owner, deleted="__missing__"):
    doc = {
        "id": nid,
        "title": nid,
        "body": "body of " + nid,
        "user_id": owner,
        "user_name": "owner",
        "timestamp": datetime.utcnow(),
        "date_string": "1 Jan 2026",
    }
    if deleted != "__missing__":
        doc["deleted"] = deleted
    return doc


@pytest.fixture
def seeded(app_module, db):
    _make_user(app_module, ALICE_ID, "alice@example.com")
    _make_user(app_module, BOB_ID, "bob@example.com")
    _make_user(app_module, CAROL_ID, "carol@example.com")

    db.messages.insert_many([
        # Alice -> Bob
        _message_doc("m-visible", ALICE_ID, BOB_ID),
        _message_doc("m-deleted", ALICE_ID, BOB_ID, deleted=True),
        # old documents: missing field and explicit null / False
        _message_doc("m-missing-flag", ALICE_ID, BOB_ID),
        _message_doc("m-null-flag", ALICE_ID, BOB_ID, deleted=None),
        _message_doc("m-false-flag", ALICE_ID, BOB_ID, deleted=False),
        # private drafts: recipient missing or empty - author only
        _message_doc("m-private-missing", ALICE_ID, None),
        _message_doc("m-private-empty", ALICE_ID, ""),
        # hidden from sender: still in recipient's inbox, not in sent
        _message_doc("m-hidden", ALICE_ID, BOB_ID, hidden=True),
    ])
    db.notes.insert_many([
        _note_doc("n-visible", ALICE_ID),
        _note_doc("n-missing-flag", ALICE_ID),
        _note_doc("n-null-flag", ALICE_ID, deleted=None),
        _note_doc("n-false-flag", ALICE_ID, deleted=False),
        _note_doc("n-deleted", ALICE_ID, deleted=True),
        _note_doc("n-bob", BOB_ID),
    ])
    return app_module


def test_unauthorized_read_and_delete_return_404(app_module, seeded):
    app = app_module.app

    # Bob (recipient) can read; Carol (a third party) cannot
    with app.test_client() as client:
        _login(client, "bob@example.com")
        assert client.get("/messages/m-visible").status_code == 200

    with app.test_client() as client:
        _login(client, "carol@example.com")
        assert client.get("/messages/m-visible").status_code == 404
        response = client.post("/delete_message", data={"message_id": "m-visible"})
        assert response.status_code == 404
        response = client.post("/delete_note", data={"note_id": "n-visible"})
        assert response.status_code == 404

    # A non-existent id is indistinguishable from an unauthorized one
    with app.test_client() as client:
        _login(client, "carol@example.com")
        assert client.get("/messages/does-not-exist").status_code == 404

    # Nothing was deleted by the unauthorized attempts
    assert app_module.mongo.db.messages.find_one({"id": "m-visible"}).get("deleted") != True


def test_missing_or_empty_recipient_is_private(app_module, seeded):
    app = app_module.app

    # Bob is not the recipient of a private draft
    with app.test_client() as client:
        _login(client, "bob@example.com")
        assert client.get("/messages/m-private-missing").status_code == 404
        assert client.get("/messages/m-private-empty").status_code == 404

    # The author can read their own private draft
    with app.test_client() as client:
        _login(client, "alice@example.com")
        assert client.get("/messages/m-private-missing").status_code == 200
        assert client.get("/messages/m-private-empty").status_code == 200

    # Recipient cannot hide someone else's sent message
    with app.test_client() as client:
        _login(client, "bob@example.com")
        response = client.post("/hide_sent_message", data={"message_id": "m-visible"})
        assert response.status_code == 404


def test_old_documents_with_missing_or_null_flags_are_counted(app_module, seeded):
    from message import Message
    from note import Note

    bob_messages = Message.user_overview(app_module.mongo.db.messages, BOB_ID)
    expected_inbox = [
        "m-visible", "m-missing-flag", "m-null-flag", "m-false-flag", "m-hidden",
    ]
    assert bob_messages["inbox_count"] == len(expected_inbox)
    assert sorted(m["id"] for m in bob_messages["inbox_messages"]) == sorted(expected_inbox)

    # The hidden-from-sender message leaves Alice's sent list but not Bob's inbox
    alice_messages = Message.user_overview(app_module.mongo.db.messages, ALICE_ID)
    assert "m-hidden" not in [m["id"] for m in alice_messages["sent_messages"]]
    assert alice_messages["sent_count"] == 6

    alice_notes = Note.user_overview(app_module.mongo.db.notes, ALICE_ID)
    expected_notes = ["n-visible", "n-missing-flag", "n-null-flag", "n-false-flag"]
    assert alice_notes["note_count"] == len(expected_notes)
    assert sorted(n["id"] for n in alice_notes["notes"]) == sorted(expected_notes)

    # Hidden from sender leaves the recipient's inbox untouched
    hidden = next(m for m in bob_messages["inbox_messages"] if m["id"] == "m-hidden")
    assert hidden["hidden_for_sender"] is True


def test_list_and_counts_come_from_one_snapshot(app_module, seeded, monkeypatch):
    # Simulate a concurrent delete between the list query and a hypothetical
    # second count query: with a single aggregation the figures still match.
    original_aggregate = app_module.mongo.db.messages.aggregate

    def race_aggregate(pipeline, *args, **kwargs):
        result = list(original_aggregate(pipeline, *args, **kwargs))
        app_module.mongo.db.messages.update_one(
            {"id": "m-visible"}, {"$set": {"deleted": True}})
        return iter(result)

    monkeypatch.setattr(app_module.mongo.db.messages, "aggregate", race_aggregate)

    app = app_module.app
    with app.test_client() as client:
        _login(client, "bob@example.com")
        page = client.get("/messages")
        assert page.status_code == 200
        html = page.get_data(as_text=True)

    # Badge number equals the number of rendered inbox cards
    import re
    badges = re.findall(r'Inbox <span class="badge[^"]*">(\d+)</span>', html)
    assert badges == ["5"]
    inbox_cards = html.count('name="message_id"')
    # 5 inbox cards (hidden-from-sender message still delivered) + 0 sent cards
    assert inbox_cards == 5


def test_profile_shows_the_same_count_twice(app_module, seeded):
    app = app_module.app
    with app.test_client() as client:
        _login(client, "alice@example.com")
        page = client.get("/profile")
        assert page.status_code == 200
        html = page.get_data(as_text=True)

    import re
    counts = re.findall(r'class="[^"]*profile-note-count[^"]*"[^>]*>(\d+)<', html)
    assert len(counts) == 2
    assert counts[0] == counts[1] == "4"

    # Rendered delete forms correspond exactly to the visible notes
    assert html.count('class="delete-note-form"') == 4


def test_unauthorized_records_appear_nowhere(app_module, seeded):
    app = app_module.app
    with app.test_client() as client:
        _login(client, "carol@example.com")
        messages_page = client.get("/messages").get_data(as_text=True)
        profile_page = client.get("/profile").get_data(as_text=True)

    assert "body of " not in messages_page
    assert "n-visible" not in profile_page
    assert "m-visible" not in messages_page
