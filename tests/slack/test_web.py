from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from app.slack.web import (
    download_file,
    download_files,
    files_list_simple,
    get_bot_accessible_files,
    get_file_info,
    get_mt_ts_cached,
    set_mt_ts_edit,
    upload_file_to_slack_memory_efficient,
)


class TestFilesListSimple:
    """Tests for files_list_simple function."""

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    @patch("app.slack.web.map_file_options")
    async def test_files_list_simple_success(self, mock_map_file_options, mock_redis):
        """Test successful file listing."""
        mock_client = AsyncMock()
        mock_files = [{"id": "F123", "name": "test.txt"}]
        mock_client.files_list.return_value = {"files": mock_files}
        mock_map_file_options.return_value = (mock_files, [])
        mock_redis.set = AsyncMock()

        result = await files_list_simple(mock_client, "C123", count=50)

        assert result == mock_files
        mock_client.files_list.assert_called_once_with(
            channel="C123", count=50, show_files_hidden_by_limit=False
        )
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    @patch("app.slack.web.map_file_options")
    async def test_files_list_simple_redis_error(
        self, mock_map_file_options, mock_redis
    ):
        """Test that redis errors don't break the function."""
        mock_client = AsyncMock()
        mock_files = [{"id": "F123", "name": "test.txt"}]
        mock_client.files_list.return_value = {"files": mock_files}
        mock_map_file_options.return_value = (mock_files, [])
        mock_redis.set = AsyncMock(side_effect=Exception("Redis error"))

        result = await files_list_simple(mock_client, "C123")

        assert result == mock_files  # Should still return files despite redis error

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    @patch("app.slack.web.map_file_options")
    async def test_files_list_simple_empty_files(
        self, mock_map_file_options, mock_redis
    ):
        """Test with empty file list."""
        mock_client = AsyncMock()
        mock_client.files_list.return_value = {"files": []}
        mock_map_file_options.return_value = ([], [])
        mock_redis.set = AsyncMock()

        result = await files_list_simple(mock_client, "C123")

        assert result == []


class TestGetFileInfo:
    """Tests for get_file_info function."""

    @pytest.mark.asyncio
    async def test_get_file_info_single_file(self):
        """Test getting info for a single file."""
        mock_client = AsyncMock()
        mock_file = {"id": "F123", "name": "test.txt"}
        mock_client.files_info.return_value = {"file": mock_file}

        result = await get_file_info(mock_client, ["F123"])

        assert len(result) == 1
        assert result[0] == mock_file

    @pytest.mark.asyncio
    async def test_get_file_info_multiple_files(self):
        """Test getting info for multiple files."""
        mock_client = AsyncMock()
        mock_files = [
            {"id": "F123", "name": "test1.txt"},
            {"id": "F456", "name": "test2.txt"},
        ]
        mock_client.files_info.side_effect = [
            {"file": mock_files[0]},
            {"file": mock_files[1]},
        ]

        result = await get_file_info(mock_client, ["F123", "F456"])

        assert len(result) == 2
        assert result[0] == mock_files[0]
        assert result[1] == mock_files[1]

    @pytest.mark.asyncio
    async def test_get_file_info_with_exception(self):
        """Test handling exceptions when getting file info."""
        mock_client = AsyncMock()
        mock_file = {"id": "F123", "name": "test.txt"}
        error = SlackApiError("File not found", response={"error": "file_not_found"})
        mock_client.files_info.side_effect = [{"file": mock_file}, error]

        result = await get_file_info(mock_client, ["F123", "F456"])

        assert len(result) == 2
        assert result[0] == mock_file
        assert isinstance(result[1], SlackApiError)

    @pytest.mark.asyncio
    async def test_get_file_info_empty_list(self):
        """Test with empty file list."""
        mock_client = AsyncMock()

        result = await get_file_info(mock_client, [])

        assert result == []


class TestGetBotAccessibleFiles:
    """Tests for get_bot_accessible_files function."""

    @pytest.mark.asyncio
    async def test_get_bot_accessible_files_success(self):
        """Test getting accessible files."""
        mock_client = AsyncMock()
        mock_files = [
            {"id": "F123", "name": "test1.txt"},
            {"id": "F456", "name": "test2.txt"},
        ]
        mock_client.files_info.side_effect = [
            {"file": mock_files[0]},
            {"file": mock_files[1]},
        ]

        result = await get_bot_accessible_files(mock_client, ["F123", "F456"])

        assert len(result) == 2
        assert result == mock_files

    @pytest.mark.asyncio
    async def test_get_bot_accessible_files_filters_exceptions(self):
        """Test that exceptions are filtered out."""
        mock_client = AsyncMock()
        mock_file = {"id": "F123", "name": "test.txt"}
        error = SlackApiError("File not found", response={"error": "file_not_found"})
        mock_client.files_info.side_effect = [{"file": mock_file}, error]

        result = await get_bot_accessible_files(mock_client, ["F123", "F456"])

        assert len(result) == 1
        assert result[0] == mock_file


class TestDownloadFile:
    """Tests for download_file function."""

    @pytest.mark.asyncio
    async def test_download_file_success(self, tmp_path):
        """Test successful file download."""
        mock_client = AsyncMock()
        mock_client.token = "xoxb-token"
        mock_file = {
            "id": "F123",
            "title": "test.txt",
            "url_private": "https://files.slack.com/files-pri/F123/test.txt",
        }
        mock_client.files_info.return_value = {"file": mock_file}

        # Create async iterator for aiter_bytes
        async def async_bytes():
            yield b"file content"

        # Mock httpx response
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = MagicMock(return_value=async_bytes())

        # Create proper async context manager for stream
        mock_stream_context = AsyncMock()
        mock_stream_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_context.__aexit__ = AsyncMock(return_value=None)

        mock_http = AsyncMock()
        mock_http.stream = MagicMock(return_value=mock_stream_context)
        mock_http.is_closed = False

        with patch("builtins.open", create=True) as mock_open:
            mock_file_obj = MagicMock()
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_file_obj)
            mock_open.return_value.__exit__ = MagicMock(return_value=None)

            result = await download_file(mock_client, "F123", http=mock_http)

            assert "test.txt" in result
            assert "F123" in result
            mock_file_obj.write.assert_called()

    @pytest.mark.asyncio
    async def test_download_file_slack_api_error(self):
        """Test handling Slack API errors."""
        mock_client = AsyncMock()
        error = SlackApiError("File not found", response={"error": "file_not_found"})
        mock_client.files_info.side_effect = error

        with pytest.raises(SlackApiError):
            await download_file(mock_client, "F123")

    @pytest.mark.asyncio
    async def test_download_file_creates_http_client(self, tmp_path):
        """Test that http client is created if not provided."""
        mock_client = AsyncMock()
        mock_client.token = "xoxb-token"
        mock_file = {
            "id": "F123",
            "title": "test.txt",
            "url_private": "https://files.slack.com/files-pri/F123/test.txt",
        }
        mock_client.files_info.return_value = {"file": mock_file}

        # Create async iterator for aiter_bytes
        async def async_bytes():
            yield b"file content"

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = MagicMock(return_value=async_bytes())

        # Create proper async context manager for stream
        mock_stream_context = AsyncMock()
        mock_stream_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_context.__aexit__ = AsyncMock(return_value=None)

        mock_http = AsyncMock()
        mock_http.stream = MagicMock(return_value=mock_stream_context)
        mock_http.is_closed = False
        mock_http.aclose = AsyncMock()

        with patch("app.slack.web.httpx.AsyncClient", return_value=mock_http):
            with patch("builtins.open", create=True) as mock_open:
                mock_file_obj = MagicMock()
                mock_open.return_value.__enter__ = MagicMock(return_value=mock_file_obj)
                mock_open.return_value.__exit__ = MagicMock(return_value=None)

                await download_file(mock_client, "F123", http=None)

                mock_http.aclose.assert_called_once()


class TestDownloadFiles:
    """Tests for download_files function."""

    @pytest.mark.asyncio
    @patch("app.slack.web.download_file")
    async def test_download_files_success(self, mock_download_file):
        """Test successful download of multiple files."""
        mock_client = AsyncMock()
        mock_download_file.side_effect = ["/tmp/file1.txt", "/tmp/file2.txt"]

        result = await download_files(mock_client, ["F123", "F456"])

        assert len(result) == 2
        assert "/tmp/file1.txt" in result
        assert "/tmp/file2.txt" in result

    @pytest.mark.asyncio
    @patch("app.slack.web.download_file")
    @patch("app.slack.web.buglog.notify_exception")
    async def test_download_files_with_exceptions(
        self, mock_notify, mock_download_file
    ):
        """Test handling exceptions during download."""
        mock_client = AsyncMock()
        error = Exception("Download failed")
        mock_download_file.side_effect = ["/tmp/file1.txt", error]

        result = await download_files(mock_client, ["F123", "F456"])

        assert len(result) == 1
        assert result[0] == "/tmp/file1.txt"
        mock_notify.assert_called_once()


class TestUploadFileToSlackMemoryEfficient:
    """Tests for upload_file_to_slack_memory_efficient function."""

    @pytest.mark.asyncio
    async def test_upload_file_success(self, tmp_path):
        """Test successful file upload."""
        # Create a temporary file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        mock_client = AsyncMock()
        mock_client.files_getUploadURLExternal = AsyncMock(
            return_value={
                "ok": True,
                "upload_url": "https://upload.slack.com/upload",
                "file_id": "F123",
            }
        )
        mock_client.files_completeUploadExternal = AsyncMock(
            return_value={"ok": True, "file": {"id": "F123"}}
        )

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.text = "OK"

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_http_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.slack.web.httpx.AsyncClient", return_value=mock_http_client):
            result = await upload_file_to_slack_memory_efficient(
                mock_client, str(test_file), "C123", title="Test File"
            )

            assert result["ok"] is True
            mock_client.files_getUploadURLExternal.assert_called_once()
            mock_client.files_completeUploadExternal.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_file_not_found(self):
        """Test upload with non-existent file."""
        mock_client = AsyncMock()

        with pytest.raises(FileNotFoundError):
            await upload_file_to_slack_memory_efficient(
                mock_client, "/nonexistent/file.txt", "C123"
            )

    @pytest.mark.asyncio
    async def test_upload_file_uses_basename(self, tmp_path):
        """Test that filename defaults to basename."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        mock_client = AsyncMock()
        mock_client.files_getUploadURLExternal = AsyncMock(
            return_value={
                "ok": True,
                "upload_url": "https://upload.slack.com/upload",
                "file_id": "F123",
            }
        )
        mock_client.files_completeUploadExternal = AsyncMock(return_value={"ok": True})

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_http_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.slack.web.httpx.AsyncClient", return_value=mock_http_client):
            await upload_file_to_slack_memory_efficient(
                mock_client, str(test_file), "C123"
            )

            # Verify filename was extracted from path
            call_args = mock_client.files_getUploadURLExternal.call_args
            assert call_args[1]["filename"] == "test.txt"

    @pytest.mark.asyncio
    async def test_upload_file_slack_api_error(self, tmp_path):
        """Test handling Slack API errors."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        mock_client = AsyncMock()
        error = SlackApiError("Upload failed", response={"error": "upload_failed"})
        mock_client.files_getUploadURLExternal = AsyncMock(side_effect=error)

        with pytest.raises(SlackApiError):
            await upload_file_to_slack_memory_efficient(
                mock_client, str(test_file), "C123"
            )


class TestSetMtTsEdit:
    """Tests for set_mt_ts_edit function."""

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    async def test_set_mt_ts_edit_success(self, mock_redis):
        """Test successful setting of mt timestamp."""
        mock_redis.set = AsyncMock()

        result = await set_mt_ts_edit("123.456", "789.012")

        assert result == "789.012"
        mock_redis.set.assert_called_once_with(
            "slack-ray-translator:mt_ts:123.456", "789.012", ex=3600
        )

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    @patch("app.slack.web.buglog.notify_exception")
    async def test_set_mt_ts_edit_redis_error(self, mock_notify, mock_redis):
        """Test handling redis errors."""
        mock_redis.set = AsyncMock(side_effect=Exception("Redis error"))

        result = await set_mt_ts_edit("123.456", "789.012")

        assert result == "789.012"  # Should still return the value
        mock_notify.assert_called_once()


class TestGetMtTsCached:
    """Tests for get_mt_ts_cached function."""

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    async def test_get_mt_ts_cached_found(self, mock_redis):
        """Test getting cached timestamp."""
        mock_redis.get = AsyncMock(return_value="789.012")

        result = await get_mt_ts_cached("123.456")

        assert result == "789.012"
        mock_redis.get.assert_called_once_with("slack-ray-translator:mt_ts:123.456")

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    async def test_get_mt_ts_cached_not_found(self, mock_redis):
        """Test when timestamp is not cached."""
        mock_redis.get = AsyncMock(return_value=None)

        result = await get_mt_ts_cached("123.456")

        assert result == ""

    @pytest.mark.asyncio
    @patch("app.slack.web.redis_conn")
    @patch("app.slack.web.buglog.notify_exception")
    async def test_get_mt_ts_cached_redis_error(self, mock_notify, mock_redis):
        """Test handling redis errors."""
        mock_redis.get = AsyncMock(side_effect=Exception("Redis error"))

        result = await get_mt_ts_cached("123.456")

        assert result == ""
        mock_notify.assert_called_once()
