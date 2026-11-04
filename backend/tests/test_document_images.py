"""Image upload/locator contracts; Vision stub is explicitly offline."""
import io
import unittest
from unittest.mock import patch
from fastapi import UploadFile
from starlette.datastructures import Headers
from PIL import Image
from app.core.learning_documents import validate_document
from app.core.exceptions import AppError
from app.services.document_pipeline import extract


class ImageContracts(unittest.IsolatedAsyncioTestCase):
    async def test_signature_mime_and_original_locator(self):
        for extension, fmt, mime in [('.png','PNG','image/png'),('.jpg','JPEG','image/jpeg'),('.jpeg','JPEG','image/jpeg')]:
            with Image.new('RGB',(64,64),'white') as image:
                body=io.BytesIO();image.save(body,fmt);data=body.getvalue()
            upload=UploadFile(filename='fixture'+extension,file=io.BytesIO(data),headers=Headers({'content-type':mime}))
            result=await validate_document(upload)
            self.assertEqual(result[1],extension);result[3].close()
            with patch('app.services.document_vision.extract_page',return_value={'text':'Measured 37','visual_description':'chart','warning':''}) as api:
                units=extract(data,extension)
            api.assert_called_once()
            self.assertEqual(units[0][1]['image_id'],'original')
            self.assertNotIn('page',units[0][1])
            self.assertEqual(units[0][1]['coverage'],'complete')

    async def test_disguised_image_rejected(self):
        upload=UploadFile(filename='fake.png',file=io.BytesIO(b'%PDF-1.4'),headers=Headers({'content-type':'image/png'}))
        with self.assertRaises(AppError):
            await validate_document(upload)
