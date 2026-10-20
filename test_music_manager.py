import os
import tempfile
import unittest
from io import BytesIO

os.environ["MUSIC_ADMIN_TOKEN"] = "test-secret"
from music_manager import app


class MusicManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import music_manager
        music_manager.DATA_DIR = __import__("pathlib").Path(self.tmp.name)
        music_manager.STAGING_DIR = music_manager.DATA_DIR / "staging"
        music_manager.STATE_FILE = music_manager.DATA_DIR / "library.json"
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-secret"}

    def tearDown(self): self.tmp.cleanup()

    def test_upload_starts_draft_and_publish_is_explicit(self):
        audio = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 20
        response = self.client.post("/api/manager/upload", headers=self.headers, data={"files": (BytesIO(audio), "demo.wav")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201)
        track = response.json["tracks"][0]
        self.assertEqual(track["status"], "draft")
        self.assertEqual(self.client.get("/api/playlist").json["tracks"], [])
        self.assertEqual(self.client.post(f"/api/manager/tracks/{track['id']}/publish", headers=self.headers).status_code, 200)
        self.assertEqual(len(self.client.get("/api/playlist").json["tracks"]), 1)

    def test_auth_is_required(self):
        self.assertEqual(self.client.get("/api/manager/library").status_code, 401)

    def test_draft_preview_is_private(self):
        audio = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 20
        track = self.client.post("/api/manager/upload", headers=self.headers, data={"files": (BytesIO(audio), "preview.wav")}, content_type="multipart/form-data").json["tracks"][0]
        self.assertEqual(self.client.get(f"/api/playlist/{track['id']}/audio").status_code, 404)
        preview = self.client.get(f"/api/manager/tracks/{track['id']}/audio", headers=self.headers)
        self.assertEqual(preview.status_code, 200)
        preview.close()

    def test_order_is_explicit_and_complete(self):
        audio = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 20
        ids = []
        for name in ("one.wav", "two.wav"):
            ids.append(self.client.post("/api/manager/upload", headers=self.headers, data={"files": (BytesIO(audio), name)}, content_type="multipart/form-data").json["tracks"][0]["id"])
        self.assertEqual(self.client.post("/api/manager/order", headers=self.headers, json={"ids": list(reversed(ids))}).status_code, 200)
        ordered = [track["id"] for track in self.client.get("/api/manager/library", headers=self.headers).json["tracks"]]
        self.assertEqual(ordered, list(reversed(ids)))

    def test_removal_preserves_audio(self):
        audio = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 20
        track = self.client.post("/api/manager/upload", headers=self.headers, data={"files": (BytesIO(audio), "keep.wav")}, content_type="multipart/form-data").json["tracks"][0]
        path = __import__("pathlib").Path(os.environ.get("MUSIC_DATA_DIR", self.tmp.name)) / "staging" / track["stored_name"]
        self.assertTrue(path.exists())
        self.assertEqual(self.client.delete(f"/api/manager/tracks/{track['id']}", headers=self.headers).status_code, 200)
        self.assertTrue(path.exists())


if __name__ == "__main__": unittest.main()
