import unittest
from unittest.mock import Mock
from urllib.parse import quote

from app.services.storage_service import StorageService


class DownloadFilenameTests(unittest.IsolatedAsyncioTestCase):
    async def test_presigned_download_uses_ascii_header_with_utf8_filename(self):
        for filename in ('note.txt', 'Luyện nói ANH VĂN 1.pdf', 'a"\r\n\\b.pdf'):
            with self.subTest(filename=filename):
                client = Mock()
                client.generate_presigned_url.return_value = 'http://storage.test/signed'
                await StorageService(client, 'private').temporary_download_url('object', filename)
                params = client.generate_presigned_url.call_args.kwargs['Params']
                disposition = params['ResponseContentDisposition']
                self.assertTrue(disposition.isascii())
                self.assertNotIn('\r', disposition)
                self.assertNotIn('\n', disposition)
                self.assertTrue(disposition.endswith("filename*=UTF-8''" + quote(filename, safe='')))
