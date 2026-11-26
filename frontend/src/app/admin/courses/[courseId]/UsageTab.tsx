"use client";

// 課程詳細頁：AI 用量與費用（#81）
// 看這門課每個月用了多少 AI（依功能、機器人、模型、日期），設定每月預算。
// 費用依價目表估算，實際以 Google Cloud 帳單為準；學生自備金鑰的費用不算進預算。
// 後端：GET /api/courses/{id}/ai-usage、PATCH /api/courses/{id}（monthly_ai_budget_usd）

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/utils/api";

type Bucket = { calls: number; failed: number; input_tokens: number; output_tokens: number; cost_usd: number; platform_cost_usd: number; student_key_cost_usd: number };
type Named = Bucket & { name: string };
type Usage = {
  month: string;
  budget: { used_usd: number; budget_usd: number; ratio: number; level: "ok" | "warn" | "exceeded" } | null;
  monthly_budget_usd: number | null;
  total: Bucket;
  by_feature: Named[];
  by_tool: Named[];
  by_model: Named[];
  by_day: (Bucket & { day: string })[];
  note: string;
};

const input: React.CSSProperties = { boxSizing: "border-box", padding: "6px 8px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "#0f172a", color: "#fff", fontSize: "13px" };
const smallBtn: React.CSSProperties = { padding: "5px 12px", fontSize: "12px", borderRadius: "6px", border: "none", backgroundColor: "#16a34a", color: "#fff", cursor: "pointer" };
const box: React.CSSProperties = { padding: "12px", borderRadius: "10px", border: "1px solid rgba(255,255,255,0.08)", backgroundColor: "rgba(255,255,255,0.03)" };

const usd = (v: number) => `US$${v < 1 ? v.toFixed(4) : v.toFixed(2)}`;
const thisMonth = () => {
  const d = new Date(Date.now() + 8 * 3600 * 1000); // 台灣時間
  return d.toISOString().slice(0, 7);
};

function Table({ title, rows }: { title: string; rows: Named[] }) {
  if (rows.length === 0) return null;
  return (
    <div style={box}>
      <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: "0 0 8px" }}>{title}</h4>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
          <thead>
            <tr style={{ color: "#94a3b8", textAlign: "left" }}>
              <th style={{ padding: "4px 6px" }}>項目</th>
              <th style={{ padding: "4px 6px", textAlign: "right" }}>次數</th>
              <th style={{ padding: "4px 6px", textAlign: "right" }}>失敗</th>
              <th style={{ padding: "4px 6px", textAlign: "right" }}>平台費用</th>
              <th style={{ padding: "4px 6px", textAlign: "right" }}>學生自備金鑰</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name} style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                <td style={{ padding: "5px 6px" }}>{r.name}</td>
                <td style={{ padding: "5px 6px", textAlign: "right" }}>{r.calls.toLocaleString()}</td>
                <td style={{ padding: "5px 6px", textAlign: "right", color: r.failed ? "#fbbf24" : undefined }}>{r.failed}</td>
                <td style={{ padding: "5px 6px", textAlign: "right" }}>{usd(r.platform_cost_usd)}</td>
                <td style={{ padding: "5px 6px", textAlign: "right", color: "#94a3b8" }}>{usd(r.student_key_cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function UsageTab({ courseId }: { courseId: string }) {
  const [month, setMonth] = useState(thisMonth);
  const [data, setData] = useState<Usage | null>(null);
  const [error, setError] = useState("");
  const [budgetInput, setBudgetInput] = useState("");
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    apiFetch<{ data: Usage }>(`/api/courses/${courseId}/ai-usage?month=${month}`).then(
      (res) => {
        if (!active) return;
        setData(res.data);
        setError("");
        setBudgetInput(res.data.monthly_budget_usd == null ? "" : String(res.data.monthly_budget_usd));
      },
      (err) => active && setError(err instanceof ApiError ? err.message : "讀不到 AI 用量")
    );
    return () => {
      active = false;
    };
  }, [courseId, month, reloadKey]);

  const saveBudget = async () => {
    const trimmed = budgetInput.trim();
    const value = trimmed === "" ? null : Number(trimmed);
    if (value !== null && (!Number.isFinite(value) || value < 0)) {
      setNotice({ ok: false, text: "預算要是 0 以上的數字，留空代表不限" });
      return;
    }
    try {
      await apiFetch(`/api/courses/${courseId}`, { method: "PATCH", body: JSON.stringify({ monthly_ai_budget_usd: value }) });
      setNotice({ ok: true, text: value === null ? "已改成不限預算" : `每月預算已設為 US$${value}` });
      setReloadKey((k) => k + 1);
    } catch (err) {
      setNotice({ ok: false, text: err instanceof ApiError ? err.message : "儲存失敗" });
    }
  };

  const b = data?.budget;
  const barColor = b?.level === "exceeded" ? "#ef4444" : b?.level === "warn" ? "#f59e0b" : "#22c55e";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
      <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ fontSize: "13px", display: "flex", gap: "8px", alignItems: "center" }}>
          月份
          <input type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={input} />
        </label>
        <span style={{ fontSize: "11px", color: "#64748b" }}>{data?.note}</span>
      </div>

      {error && <p style={{ margin: 0, fontSize: "13px", color: "#fca5a5" }}>{error}</p>}

      <div style={box}>
        <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: "0 0 8px" }}>每月預算（平台金鑰）</h4>
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap", fontSize: "13px" }}>
          US$
          <input type="number" min={0} step="0.5" placeholder="不限" value={budgetInput} onChange={(e) => setBudgetInput(e.target.value)} style={{ ...input, width: "110px" }} />
          <button type="button" onClick={saveBudget} style={smallBtn}>儲存</button>
          {notice && <span style={{ fontSize: "12px", color: notice.ok ? "#86efac" : "#fca5a5" }}>{notice.text}</span>}
        </div>
        <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#94a3b8" }}>
          用到 8 成會提醒；用完後學生不能開始新的作答或聊天訊息，但已經聊完的可以送出評分。學生自備金鑰不受影響。
        </p>
        {b && (
          <div style={{ marginTop: "10px" }}>
            <div style={{ height: "8px", borderRadius: "9999px", backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
              <div style={{ width: `${Math.min(b.ratio, 1) * 100}%`, height: "100%", backgroundColor: barColor }} />
            </div>
            <p style={{ margin: "6px 0 0", fontSize: "12px", color: barColor }}>
              本月已用 {usd(b.used_usd)} / US${b.budget_usd}（{Math.round(b.ratio * 100)}%）
              {b.level === "exceeded" ? "・已用完，學生暫時不能開始新的練習" : b.level === "warn" ? "・快用完了" : ""}
            </p>
          </div>
        )}
      </div>

      {data && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "10px" }}>
            {[
              ["平台費用", usd(data.total.platform_cost_usd)],
              ["學生自備金鑰", usd(data.total.student_key_cost_usd)],
              ["AI 呼叫次數", data.total.calls.toLocaleString()],
              ["失敗次數", String(data.total.failed)],
            ].map(([label, value]) => (
              <div key={label} style={box}>
                <div style={{ fontSize: "11px", color: "#94a3b8" }}>{label}</div>
                <div style={{ fontSize: "20px", fontWeight: "bold", marginTop: "4px" }}>{value}</div>
              </div>
            ))}
          </div>
          {data.total.calls === 0 && <p style={{ margin: 0, fontSize: "13px", color: "#94a3b8" }}>這個月還沒有 AI 用量紀錄。</p>}
          <Table title="依功能" rows={data.by_feature} />
          <Table title="依機器人" rows={data.by_tool} />
          <Table title="依模型" rows={data.by_model} />
          <Table title="依日期" rows={data.by_day.map((d) => ({ ...d, name: d.day }))} />
        </>
      )}
    </div>
  );
}
