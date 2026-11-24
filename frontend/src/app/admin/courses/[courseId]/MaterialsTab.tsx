"use client";

// 課程詳細頁：教材（學習助教 RAG，#67）
// 老師放進來的教材會切段、建索引；聊天室裡勾了「學習助教」的機器人回答前會先查這門課的教材，並標出引用段落。
// 可以貼上文字、選 .txt / .md（先放進文字框再加入），或直接上傳 pdf、docx、pptx（後端取出文字，#66）。
// 後端：/api/courses/{course_id}/materials、/materials/upload（backend/api/materials.py、core/file_text.py）

import { useEffect, useState } from "react";
import { ApiError, apiFetch, apiUpload } from "@/utils/api";

type Material = { id: string; title: string; source_type: string; char_count: number; chunk_count: number; created_at: string };
type Chunk = { chunk_index: number; content: string };

const MAX_CHARS = 200_000;
const MAX_FILE_MB = 20;
const SOURCE_LABEL: Record<string, string> = { pdf: "PDF", docx: "Word", pptx: "PowerPoint", text: "文字" };
const input: React.CSSProperties = { width: "100%", boxSizing: "border-box", padding: "8px 10px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "#0f172a", color: "#fff", fontSize: "13px", outline: "none" };
const smallBtn: React.CSSProperties = { padding: "4px 10px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "transparent", color: "#cbd5e1", cursor: "pointer" };

export default function MaterialsTab({ courseId }: { courseId: string }) {
  const [items, setItems] = useState<Material[] | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [preview, setPreview] = useState<{ id: string; chunks: Chunk[] } | null>(null);

  const base = `/api/courses/${courseId}/materials`;
  const [reloadKey, setReloadKey] = useState(0); // 新增、刪除後 +1 重新讀清單
  const load = () => setReloadKey((k) => k + 1);

  useEffect(() => {
    let active = true;
    apiFetch<{ data: Material[] }>(base).then(
      (res) => active && setItems(res.data),
      (err) => {
        if (!active) return;
        setItems([]);
        setNotice({ ok: false, text: err instanceof ApiError ? err.message : "讀不到教材清單" });
      }
    );
    return () => {
      active = false;
    };
  }, [base, reloadKey]);

  const errorText = (err: unknown, fallback: string) => {
    const types = err instanceof ApiError ? ((err.detail as { detected_types?: string[] })?.detected_types ?? []) : [];
    return `${err instanceof ApiError ? err.message : fallback}${types.length ? `（偵測到：${types.join("、")}）` : ""}`;
  };

  // .txt / .md：放進文字框，老師可以先改再加入；pdf、docx、pptx：直接上傳，由後端取出文字
  const readFile = async (input: HTMLInputElement) => {
    const file = input.files?.[0];
    input.value = ""; // 同一個檔案再選一次也會觸發
    if (!file) return;
    if (/\.(txt|md)$/i.test(file.name)) {
      const text = await file.text();
      setContent(text.slice(0, MAX_CHARS));
      if (!title) setTitle(file.name.replace(/\.(txt|md)$/i, ""));
      return;
    }
    if (!/\.(pdf|docx|pptx)$/i.test(file.name)) {
      setNotice({ ok: false, text: "只支援 pdf、docx、pptx、txt、md；舊版 .doc / .ppt 請另存成新格式" });
      return;
    }
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      setNotice({ ok: false, text: `檔案太大（上限 ${MAX_FILE_MB} MB）` });
      return;
    }
    setBusy(true);
    setNotice({ ok: true, text: `正在讀取「${file.name}」並建立索引，頁數多的檔案要等一下…` });
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("title", title.trim());
      const res = await apiUpload<{ data: Material }>(`${base}/upload`, form);
      setTitle("");
      setNotice({ ok: true, text: `已加入「${res.data.title}」（${res.data.chunk_count} 段），學習助教之後回答會參考它` });
      load();
    } catch (err) {
      setNotice({ ok: false, text: errorText(err, "上傳失敗，請稍後再試") });
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    if (!title.trim() || !content.trim()) {
      setNotice({ ok: false, text: "請填教材名稱與內容" });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      await apiFetch(base, { method: "POST", body: JSON.stringify({ title, content }) });
      setTitle("");
      setContent("");
      setNotice({ ok: true, text: "已加入教材，學習助教之後回答會參考它" });
      load();
    } catch (err) {
      setNotice({ ok: false, text: errorText(err, "加入失敗，請稍後再試") });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (m: Material) => {
    if (!window.confirm(`確定要刪除教材「${m.title}」嗎？學習助教之後不會再引用它。`)) return;
    try {
      await apiFetch(`${base}/${m.id}`, { method: "DELETE" });
      if (preview?.id === m.id) setPreview(null);
      load();
    } catch (err) {
      setNotice({ ok: false, text: err instanceof ApiError ? err.message : "刪除失敗" });
    }
  };

  const togglePreview = async (m: Material) => {
    if (preview?.id === m.id) return setPreview(null);
    try {
      const res = await apiFetch<{ data: Chunk[] }>(`${base}/${m.id}/chunks`);
      setPreview({ id: m.id, chunks: res.data });
    } catch (err) {
      setNotice({ ok: false, text: err instanceof ApiError ? err.message : "讀不到切段結果" });
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <p style={{ margin: 0, fontSize: "13px", color: "#cbd5e1", lineHeight: 1.6 }}>
        這裡的教材給聊天室裡的「學習助教」參考：學生問問題時，助教會先找教材裡相關的段落再回答，並標出引用哪一段；找不到就會說教材裡沒有。
        要讓某個機器人變成學習助教，到「模組管理」的聊天室設定勾選「學習助教」。
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: "8px", padding: "14px", borderRadius: "10px", border: "1px dashed rgba(255,255,255,0.2)" }}>
        <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: 0 }}>加入教材</h4>
        <input maxLength={100} placeholder="教材名稱，例如：IEP 撰寫指引" value={title} onChange={(e) => setTitle(e.target.value)} style={input} />
        <textarea rows={8} maxLength={MAX_CHARS} placeholder="貼上教材文字，或在下面選檔案（pdf、docx、pptx 直接上傳；txt、md 會先放進這裡）。請勿包含真實個案的姓名、電話、身分證字號等個資。" value={content} onChange={(e) => setContent(e.target.value)} style={{ ...input, resize: "vertical", lineHeight: 1.6 }} />
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          <input
            type="file"
            disabled={busy}
            accept=".pdf,.docx,.pptx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,text/plain,text/markdown"
            onChange={(e) => readFile(e.currentTarget)}
            style={{ fontSize: "12px", color: "#cbd5e1" }}
          />
          <span style={{ fontSize: "11px", color: "#64748b" }}>{content.length.toLocaleString()} / {MAX_CHARS.toLocaleString()} 字</span>
          <button type="button" disabled={busy} onClick={add} style={{ ...smallBtn, marginLeft: "auto", backgroundColor: "#16a34a", color: "#fff", border: "none", opacity: busy ? 0.5 : 1 }}>
            {busy ? "處理中…" : "加入教材"}
          </button>
        </div>
        {notice && <p style={{ margin: 0, fontSize: "12px", color: notice.ok ? "#86efac" : "#fca5a5" }}>{notice.text}</p>}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        <h4 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>這門課的教材（{items?.length ?? 0}）</h4>
        {items === null && <p style={{ margin: 0, fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
        {items?.length === 0 && <p style={{ margin: 0, fontSize: "13px", color: "#94a3b8" }}>還沒有教材。</p>}
        {items?.map((m) => (
          <div key={m.id} style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.08)", backgroundColor: "rgba(255,255,255,0.03)" }}>
            <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
              <strong style={{ fontSize: "13px" }}>{m.title}</strong>
              <span style={{ fontSize: "11px", color: "#94a3b8" }}>
                {SOURCE_LABEL[m.source_type] ?? m.source_type}・{m.char_count.toLocaleString()} 字・{m.chunk_count} 段・{new Date(m.created_at).toLocaleDateString("zh-TW")}
              </span>
              <span style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
                <button type="button" onClick={() => togglePreview(m)} style={smallBtn}>{preview?.id === m.id ? "收起" : "看切段"}</button>
                <button type="button" onClick={() => remove(m)} style={{ ...smallBtn, color: "#f87171" }}>刪除</button>
              </span>
            </div>
            {preview?.id === m.id && (
              <ol style={{ margin: "8px 0 0", paddingLeft: "20px", display: "flex", flexDirection: "column", gap: "6px" }}>
                {preview.chunks.map((c) => (
                  <li key={c.chunk_index} style={{ fontSize: "12px", color: "#cbd5e1", whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{c.content}</li>
                ))}
              </ol>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
