"""
tests/test_push_sessions_chunking.py
Chunk-Verhalten von push_sessions_to_garmin() (2026-08-15): bei mehr session_ids
als chunk_size wird nur der erste Chunk gepusht, die Antwort liefert
pushed_ids/remaining_ids/total_remaining fuer die naechste Runde.
coach.garmin_batch.run_batch wird durchgehend gemockt — kein echter Garmin-/DB-Call.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coach.mcp_server import push_sessions_to_garmin

_EMPTY_RESULT = {"created": [], "updated": [], "moved": [], "unchanged": [], "failed": []}


class TestPushSessionsChunking:
    def test_fewer_than_chunk_size_pushes_all(self):
        with patch("coach.garmin_batch.run_batch") as m:
            m.return_value = dict(_EMPTY_RESULT)
            result = push_sessions_to_garmin([1, 2], chunk_size=7)

        m.assert_called_once_with(session_ids=[1, 2])
        assert result["pushed_ids"] == [1, 2]
        assert result["remaining_ids"] == []
        assert result["total_remaining"] == 0

    def test_more_than_chunk_size_pushes_first_chunk_only(self):
        session_ids = list(range(1, 11))
        with patch("coach.garmin_batch.run_batch") as m:
            m.return_value = dict(_EMPTY_RESULT)
            result = push_sessions_to_garmin(session_ids, chunk_size=7)

        m.assert_called_once_with(session_ids=[1, 2, 3, 4, 5, 6, 7])
        assert result["pushed_ids"] == [1, 2, 3, 4, 5, 6, 7]
        assert result["remaining_ids"] == [8, 9, 10]
        assert result["total_remaining"] == 3

    def test_default_chunk_size_is_seven(self):
        session_ids = list(range(1, 9))
        with patch("coach.garmin_batch.run_batch") as m:
            m.return_value = dict(_EMPTY_RESULT)
            result = push_sessions_to_garmin(session_ids)

        assert len(result["pushed_ids"]) == 7
        assert result["remaining_ids"] == [8]
        assert result["total_remaining"] == 1

    def test_custom_chunk_size(self):
        with patch("coach.garmin_batch.run_batch") as m:
            m.return_value = dict(_EMPTY_RESULT)
            result = push_sessions_to_garmin([1, 2, 3, 4, 5], chunk_size=2)

        m.assert_called_once_with(session_ids=[1, 2])
        assert result["remaining_ids"] == [3, 4, 5]
        assert result["total_remaining"] == 3

    def test_exception_returns_all_ids_as_remaining(self):
        with patch("coach.garmin_batch.run_batch") as m:
            m.side_effect = Exception("Garmin down")
            result = push_sessions_to_garmin([1, 2, 3], chunk_size=7)

        assert result["error"] == "Garmin down"
        assert result["pushed_ids"] == []
        assert result["remaining_ids"] == [1, 2, 3]
        assert result["total_remaining"] == 3

    def test_result_fields_added_without_losing_original_keys(self):
        with patch("coach.garmin_batch.run_batch") as m:
            m.return_value = {"created": [42], "updated": [], "moved": [], "unchanged": [], "failed": []}
            result = push_sessions_to_garmin([42], chunk_size=7)

        assert result["created"] == [42]
        assert result["pushed_ids"] == [42]
