"""
#66 → #67：上傳 pdf、docx、pptx、txt、md 當學習助教的教材

檔案在測試裡現做（python-docx、python-pptx、手寫最小 PDF），不讀外部檔案；向量一律 Mock。
"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from api import materials as materials_api
from core import materials
from core.auth import CurrentUser, get_current_user
from core.file_text import FileTextError, detect_type, extract_text
from tests.inmemory_db import InMemoryDB

COURSE = "c0000000-0000-4000-8000-000000000001"
PROFESSOR = CurrentUser(id="prof-1", role="owner")


def make_docx(paragraphs, table=None) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    if table:
        t = doc.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                t.cell(r, c).text = value
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_pptx(slides, notes=None) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for i, text in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = f"第 {i + 1} 張"
        box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(2))
        box.text_frame.text = text
        if notes and i < len(notes):
            slide.notes_slide.notes_text_frame.text = notes[i]
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_pdf(text: str) -> bytes:
    """最小的單頁 PDF（ASCII 文字，Helvetica），xref 位移依實際位置計算"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


# ──────────────────────────────────────────
# 取文字
# ──────────────────────────────────────────

class TestExtract:

    def test_docx_paragraphs_and_tables(self):
        text, kind = extract_text("指引.docx", make_docx(["長期目標要可測量", "短期目標要具體"], [["項目", "說明"], ["目標", "可觀察"]]))
        assert kind == "docx"
        assert "長期目標要可測量" in text and "目標｜可觀察" in text

    def test_pptx_slides_and_notes(self):
        text, kind = extract_text("課程.pptx", make_pptx(["功能性現況要具體"], notes=["舉一個例子"]))
        assert kind == "pptx"
        assert "（投影片 1）" in text and "功能性現況要具體" in text and "講者備註：舉一個例子" in text

    def test_pdf_text(self):
        text, kind = extract_text("guide.pdf", make_pdf("IEP goals must be measurable"))
        assert kind == "pdf"
        assert "IEP goals must be measurable" in text

    def test_utf8_text_and_markdown(self):
        assert extract_text("notes.md", "# 標題\n內容".encode()) == ("# 標題\n內容", "text")
        assert extract_text("bom.txt", "﻿內容".encode("utf-8")) == ("內容", "text")

    def test_type_comes_from_content_not_extension(self):
        """改副檔名騙不過：內容是 docx 就當 docx 讀"""
        assert detect_type("看起來是.pdf", make_docx(["x"])) == "docx"

    @pytest.mark.parametrize("name, data, code", [
        ("a.exe", b"MZ\x90\x00", "UNSUPPORTED_FILE"),
        ("old.doc", b"\xd0\xcf\x11\xe0legacy", "UNSUPPORTED_FILE"),
        ("big5.txt", "中文".encode("big5"), "UNSUPPORTED_FILE"),
        ("empty.pdf", b"", "EMPTY_FILE"),
        ("broken.pdf", b"%PDF-1.4 garbage", "BROKEN_FILE"),
    ])
    def test_rejected_files(self, name, data, code):
        with pytest.raises(FileTextError) as e:
            extract_text(name, data)
        assert e.value.code == code

    def test_zip_that_is_not_office(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("hello.txt", "hi")
        with pytest.raises(FileTextError) as e:
            extract_text("a.docx", buf.getvalue())
        assert e.value.code == "UNSUPPORTED_FILE"

    def test_too_large(self, monkeypatch):
        from core import file_text
        monkeypatch.setattr(file_text, "MAX_FILE_BYTES", 10)
        with pytest.raises(FileTextError) as e:
            extract_text("a.txt", b"x" * 11)
        assert e.value.code == "FILE_TOO_LARGE"


# ──────────────────────────────────────────
# 上傳 API
# ──────────────────────────────────────────

@pytest.fixture(autouse=True)
def professor():
    main.app.dependency_overrides[get_current_user] = lambda: PROFESSOR
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def upload(db, filename, data, title=""):
    client = TestClient(main.app, raise_server_exceptions=False)
    vectors = AsyncMock(side_effect=lambda texts, **kw: [[0.1] * 768 for _ in texts])
    with patch.object(materials_api, "get_supabase", return_value=db), patch.object(materials, "embed_texts", vectors):
        return client.post(
            f"/api/courses/{COURSE}/materials/upload",
            files={"file": (filename, data, "application/octet-stream")},
            data={"title": title},
        )


def db():
    return InMemoryDB(courses=[{"id": COURSE}], course_materials=[], material_chunks=[])


class TestUploadApi:

    def test_docx_becomes_material(self):
        d = db()
        res = upload(d, "IEP 撰寫指引.docx", make_docx(["長期目標要可觀察、可測量。"]))

        assert res.status_code == 201
        material = res.json()["data"]
        assert material["title"] == "IEP 撰寫指引"            # 沒填名稱就用檔名
        assert material["source_type"] == "docx"
        assert "長期目標" in d.rows("material_chunks")[0]["content"]

    def test_title_overrides_filename(self):
        res = upload(db(), "slides.pptx", make_pptx(["內容"]), title="第三週講義")
        assert res.json()["data"]["title"] == "第三週講義"

    def test_pii_inside_file_is_blocked(self):
        d = db()
        res = upload(d, "個案.docx", make_docx(["家長電話 0912-345-678"]))

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "PII_DETECTED"
        assert d.rows("course_materials") == []

    def test_scanned_pdf_without_text_is_explained(self):
        res = upload(db(), "scan.pdf", make_pdf(""))
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "EMPTY_MATERIAL"

    def test_unsupported_file(self):
        res = upload(db(), "virus.exe", b"MZ\x90\x00")
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "UNSUPPORTED_FILE"

    def test_students_cannot_upload(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="s1", role="student")
        res = upload(db(), "a.txt", "內容".encode())
        assert res.status_code == 403
