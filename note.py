from datetime import datetime
import uuid

from message import is_party, FLAG_OFF


# Note class
class Note():

    def __init__(self, title, body, user_id, user_name, id="", deleted=False):
        self.title = title
        self.body = body
        self.user_id = user_id
        self.user_name = user_name
        self.timestamp = datetime.utcnow()
        self.date_string = self.timestamp.strftime("%d %b %Y")
        self.id = uuid.uuid4().hex if not id else id
        self.deleted = deleted

    # Return dictionary representation of object
    def dict(self):
        return {
            "title": self.title,
            "body": self.body,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "timestamp": self.timestamp,
            "date_string": self.date_string,
            "id": self.id,
            "deleted": self.deleted
        }

    # Same single entry point used by messages: the note is only
    # returned to its owner; anyone else gets None (caller returns 404).
    @classmethod
    def get_for_user(cls, collection, note_id, user_id):
        note = collection.find_one({"id": note_id})
        if note is None or not is_party(note, user_id):
            return None
        return note

    # Notes are private to their author. List and count come from one
    # aggregation; documents missing the "deleted" field (old documents)
    # or storing an explicit null are treated as not deleted.
    @classmethod
    def user_overview(cls, collection, user_id):
        match = {
            "user_id": user_id,
            "$or": FLAG_OFF,
        }
        result = list(collection.aggregate([
            {"$facet": {
                "notes": [
                    {"$match": match},
                    {"$sort": {"timestamp": -1}},
                ],
                "count": [
                    {"$match": match},
                    {"$count": "count"},
                ],
            }}
        ]))[0]
        count_rows = result.get("count") or [{}]
        return {
            "notes": result.get("notes", []),
            "note_count": count_rows[0].get("count", 0),
        }
