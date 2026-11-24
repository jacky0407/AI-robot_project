"""
從上傳的檔案取出文字（#66 → 學習助教教材，#67）

支援 pdf、docx、pptx、txt、md。只看檔案內容判斷格式，不只信副檔名：
  pdf  開頭是 %PDF-
  docx zip 裡有 word/document.xml
  pptx zip 裡有 ppt/presentation.xml
  txt / md 能用 UTF-8 解碼

取出的文字之後一律走 core/materials.add_material()（個資檢查 → 切段 → 向量）。
掃描的 PDF 沒有文字層，取出來是空的，由 add_material 回「沒有文字內容」。
"""

import io
import zipfile

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_PDF_PAGES = 300

SOURCE_TYPES = ("pdf", "docx", "pptx", "text")


class FileTextError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def detect_type(filename: str, data: bytes) -> str:
    """依檔案內容判斷格式（副檔名只用來區分 txt/md 與其他）"""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            names = set()
        if "word/document.xml" in names:
            return "docx"
        if "ppt/presentation.xml" in names:
            return "pptx"
        raise FileTextError("UNSUPPORTED_FILE", "只支援 pdf、docx、pptx、txt、md；這個檔案的格式無法辨識")
    if (filename or "").lower().endswith((".txt", ".md")):
        return "text"
    raise FileTextError("UNSUPPORTED_FILE", "只支援 pdf、docx、pptx、txt、md；舊版 .doc / .ppt 請另存成新格式")


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """回傳 (文字, 來源格式)。格式不對或檔案壞掉拋 FileTextError。"""
    if not data:
        raise FileTextError("EMPTY_FILE", "檔案是空的")
    if len(data) > MAX_FILE_BYTES:
        raise FileTextError("FILE_TOO_LARGE", f"檔案太大（上限 {MAX_FILE_BYTES // 1024 // 1024} MB）")

    kind = detect_type(filename, data)
    try:
        if kind == "pdf":
            text = _pdf(data)
        elif kind == "docx":
            text = _docx(data)
        elif kind == "pptx":
            text = _pptx(data)
        else:
            text = _plain(data)
    except FileTextError:
        raise
    except Exception as e:  # noqa: BLE001 - 各套件的例外型別不同，一律當檔案壞掉
        raise FileTextError("BROKEN_FILE", f"檔案讀不出來，可能已損壞或有密碼保護（{type(e).__name__}）")
    return text.strip(), kind


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise FileTextError("BROKEN_FILE", "PDF 有密碼保護，請先解除密碼再上傳")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise FileTextError("FILE_TOO_LARGE", f"PDF 太多頁（上限 {MAX_PDF_PAGES} 頁），請分成幾份上傳")
    # 每頁之間空一行，切段時會當成段落邊界
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)


def _docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append("｜".join(cell.text.strip() for cell in row.cells))
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _pptx(data: bytes) -> str:
    from pptx import Presentation

    slides = []
    for i, slide in enumerate(Presentation(io.BytesIO(data)).slides, start=1):
        lines = [f"（投影片 {i}）"]
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                lines.append(shape.text_frame.text.strip())
        notes = slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide else ""
        if notes:
            lines.append(f"講者備註：{notes}")
        if len(lines) > 1:
            slides.append("\n".join(lines))
    return "\n\n".join(slides)


def _plain(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileTextError("UNSUPPORTED_FILE", "文字檔請存成 UTF-8 編碼")
