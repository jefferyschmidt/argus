import threading

from argus.tasks.store import TaskStore


def _store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "argus.db")


def _create(store, **overrides):
    defaults = dict(title="Research X", prompt="go research X", budget_tokens=1000, budget_seconds=60.0)
    defaults.update(overrides)
    return store.create(**defaults)


def test_create_starts_queued(tmp_path):
    store = _store(tmp_path)
    task_id = _create(store)

    task = store.get(task_id)
    assert task.status == "queued"
    assert task.title == "Research X"
    assert task.budget_tokens == 1000
    assert task.budget_seconds == 60.0
    assert task.tokens_used == 0
    assert task.created_by == "user"
    assert task.started_ts is None


def test_mark_running_only_from_queued(tmp_path):
    store = _store(tmp_path)
    task_id = _create(store)

    assert store.mark_running(task_id) is True
    assert store.get(task_id).status == "running"
    assert store.get(task_id).started_ts is not None
    assert store.mark_running(task_id) is False  # already running


def test_mark_done_only_from_running(tmp_path):
    store = _store(tmp_path)
    task_id = _create(store)

    assert store.mark_done(task_id, "result") is False  # still queued
    store.mark_running(task_id)
    assert store.mark_done(task_id, "the result") is True
    task = store.get(task_id)
    assert task.status == "done"
    assert task.result_ref == "the result"
    assert task.finished_ts is not None


def test_mark_failed_from_running_or_queued(tmp_path):
    store = _store(tmp_path)
    running_id = _create(store)
    store.mark_running(running_id)
    queued_id = _create(store)

    assert store.mark_failed(running_id, "boom") is True
    assert store.mark_failed(queued_id, "interrupted by restart") is True
    assert store.get(running_id).error == "boom"
    assert store.get(queued_id).status == "failed"


def test_mark_cancelled(tmp_path):
    store = _store(tmp_path)
    task_id = _create(store)
    store.mark_running(task_id)

    assert store.mark_cancelled(task_id) is True
    assert store.get(task_id).status == "cancelled"


def test_update_progress_note_and_tokens(tmp_path):
    store = _store(tmp_path)
    task_id = _create(store)

    store.update_progress(task_id, "called search_web")
    assert store.get(task_id).progress_note == "called search_web"

    store.update_progress(task_id, "called write_file", tokens_used=500)
    task = store.get(task_id)
    assert task.progress_note == "called write_file"
    assert task.tokens_used == 500


def test_get_missing_task_returns_none(tmp_path):
    assert _store(tmp_path).get(999) is None


def test_list_queued_and_list_running(tmp_path):
    store = _store(tmp_path)
    queued_id = _create(store)
    running_id = _create(store)
    store.mark_running(running_id)

    assert [t.id for t in store.list_queued()] == [queued_id]
    assert [t.id for t in store.list_running()] == [running_id]


def test_list_all_orders_newest_first(tmp_path):
    store = _store(tmp_path)
    first = _create(store, title="first")
    second = _create(store, title="second")

    assert [t.id for t in store.list_all()] == [second, first]


def test_persists_across_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    store = TaskStore(db_path)
    task_id = _create(store)
    store.mark_running(task_id)

    reopened = TaskStore(db_path)
    assert reopened.get(task_id).status == "running"


def test_concurrent_writes_do_not_raise_database_is_locked(tmp_path):
    store = _store(tmp_path)
    errors = []

    def _work(n: int) -> None:
        try:
            for i in range(25):
                task_id = _create(store, title=f"t{n}-{i}")
                store.mark_running(task_id)
                store.update_progress(task_id, "working")
                store.mark_done(task_id, "ok")
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=_work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len([t for t in store.list_all(limit=1000) if t.status == "done"]) == 6 * 25
