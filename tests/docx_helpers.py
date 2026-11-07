"""A minimal .docx for tests: a zip with `word/document.xml` and whatever other parts a test names."""

import io
import zipfile


def docx(document, parts=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
        for name, content in (parts or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()
