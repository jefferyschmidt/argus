import threading
from unittest.mock import MagicMock, patch

from argus.knowledge_watcher import KnowledgeWatcher


def _watcher():
    speak_fn = MagicMock()
    lock = threading.Lock()
    return KnowledgeWatcher(speak_fn, lock), speak_fn


def test_first_check_ingests_existing_files_but_does_not_announce(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.pdf").write_bytes(b"not a real pdf but has the extension")
    watcher, speak_fn = _watcher()

    with patch("argus.knowledge_watcher.settings") as settings, \
         patch("argus.knowledge_watcher.ingest_file", return_value=3) as ingest_file:
        settings.knowledge_watch_folder = str(tmp_path)
        watcher.check_now()

    assert ingest_file.call_count == 2
    speak_fn.assert_not_called()


def test_new_file_after_first_check_is_announced(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    watcher, speak_fn = _watcher()

    with patch("argus.knowledge_watcher.settings") as settings, \
         patch("argus.knowledge_watcher.ingest_file", return_value=1):
        settings.knowledge_watch_folder = str(tmp_path)
        watcher.check_now()  # first check: baseline, no announcement
        (tmp_path / "b.txt").write_text("a second file")
        watcher.check_now()  # second check: b.txt is new

    speak_fn.assert_called_once()
    assert "b.txt" in speak_fn.call_args[0][0]


def test_unchanged_file_is_not_reingested(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hello")
    watcher, speak_fn = _watcher()

    with patch("argus.knowledge_watcher.settings") as settings, \
         patch("argus.knowledge_watcher.ingest_file", return_value=1) as ingest_file:
        settings.knowledge_watch_folder = str(tmp_path)
        watcher.check_now()
        watcher.check_now()

    assert ingest_file.call_count == 1


def test_unsupported_extension_is_skipped(tmp_path):
    (tmp_path / "image.png").write_bytes(b"binary junk")
    watcher, _ = _watcher()

    with patch("argus.knowledge_watcher.settings") as settings, \
         patch("argus.knowledge_watcher.ingest_file") as ingest_file:
        settings.knowledge_watch_folder = str(tmp_path)
        watcher.check_now()

    ingest_file.assert_not_called()


def test_missing_folder_is_a_noop():
    watcher, speak_fn = _watcher()

    with patch("argus.knowledge_watcher.settings") as settings, \
         patch("argus.knowledge_watcher.ingest_file") as ingest_file:
        settings.knowledge_watch_folder = "/nonexistent/folder/path"
        watcher.check_now()

    ingest_file.assert_not_called()
    speak_fn.assert_not_called()


def test_ingest_failure_on_one_file_does_not_block_others(tmp_path):
    (tmp_path / "bad.txt").write_text("bad")
    (tmp_path / "good.txt").write_text("good")
    watcher, _ = _watcher()

    def side_effect(path, *a, **kw):
        if path.name == "bad.txt":
            raise RuntimeError("boom")
        return 1

    with patch("argus.knowledge_watcher.settings") as settings, \
         patch("argus.knowledge_watcher.ingest_file", side_effect=side_effect) as ingest_file:
        settings.knowledge_watch_folder = str(tmp_path)
        watcher.check_now()

    assert ingest_file.call_count == 2
